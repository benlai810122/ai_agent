import sys
from flask import Flask, render_template, request, jsonify
from ai_agent import (
    _run_agent_turn, base_url, ALL_TOOL_FUNCTIONS, _generate_ai_report, SCRIPT_DIR,
    PENDING_TEST_SCRIPTS, get_tool_param_schema,
)
from ai_task_runner import run_test_script
from ai_task_state_manager import has_runner_state, load_runner_state
from ai_flow_model import build_flow_model, resolve_node, coerce_and_validate
import threading
import uuid
import os
import json
from datetime import datetime


MAX_UPLOAD_CHARS = 50000

# When frozen by PyInstaller, data files are in sys._MEIPASS (the temp extraction
# folder for --onefile, or the _internal/ folder for --onedir).
# The exe itself lives in os.path.dirname(sys.executable).
if getattr(sys, 'frozen', False):
    # _MEIPASS is where PyInstaller unpacks bundled data files at runtime
    _BASE_DIR = sys._MEIPASS
    # Runtime writable data (reports, etc.) should live next to the exe, not in _MEIPASS
    _RUNTIME_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _RUNTIME_DIR = _BASE_DIR

app = Flask(
    __name__,
    template_folder=os.path.join(_BASE_DIR, 'templates'),
    static_folder=os.path.join(_BASE_DIR, 'static'),
)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Shared conversation state (per session in real app)
messages = []
lock = threading.Lock()
request_states = {}
request_states_lock = threading.Lock()
active_request_id = None  # Currently auto-resumed task, surfaced to the UI on load
HISTORY_FILE = os.path.join(_RUNTIME_DIR, "report", "web_ui_run_history.json")
MAX_HISTORY_ITEMS = 100


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

def _ensure_history_dir() -> None:
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)


def _read_history() -> list:
    _ensure_history_dir()
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_history(history: list) -> None:
    _ensure_history_dir()
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history[:MAX_HISTORY_ITEMS], f, ensure_ascii=False, indent=2)


def _append_history_item(item: dict) -> None:
    history = _read_history()
    history.insert(0, item)
    _write_history(history)


# Structured flow-panel events reuse the progress transport but must not be
# persisted into the run history (the [Flow] payload is large and none of these
# are human-readable log lines).
_FLOW_EVENT_PREFIXES = ("[Flow]", "[Node]", "[Round]", "[FlowClear]")


def _strip_flow_events(progress: list) -> list:
    cleaned = []
    for item in progress or []:
        text = item.get("text", "") if isinstance(item, dict) else str(item)
        if text.startswith(_FLOW_EVENT_PREFIXES):
            continue
        cleaned.append(item)
    return cleaned


def _run_chat_request(request_id: str, user_text: str, require_confirmation: bool = True) -> None:
    def _step_logger(step_text: str) -> None:
        with request_states_lock:
            state = request_states.get(request_id)
            if state:
                state["progress"].append({
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "text": step_text,
                })

    try:
        with lock:
            reply = _run_agent_turn(
                messages,
                user_text,
                print_tool_logs=False,
                require_test_confirmation=require_confirmation,
                step_callback=_step_logger,
            )
        with request_states_lock:
            state = request_states.get(request_id)
            if state:
                state["status"] = "done"
                state["reply"] = reply
                state["ended_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _append_history_item({
                    "request_id": request_id,
                    "status": "done",
                    "user_text": state.get("user_text", ""),
                    "started_at": state.get("started_at", ""),
                    "ended_at": state.get("ended_at", ""),
                    "progress": _strip_flow_events(state.get("progress", [])),
                    "reply": reply,
                })
    except Exception as e:
        with request_states_lock:
            state = request_states.get(request_id)
            if state:
                state["status"] = "error"
                state["error"] = str(e)
                state["ended_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _append_history_item({
                    "request_id": request_id,
                    "status": "error",
                    "user_text": state.get("user_text", ""),
                    "started_at": state.get("started_at", ""),
                    "ended_at": state.get("ended_at", ""),
                    "progress": _strip_flow_events(state.get("progress", [])),
                    "reply": "",
                    "error": str(e),
                })

@app.route('/')
def index():
    return render_template('chat.html')

@app.route('/chat', methods=['POST'])
def chat():
    user_text = ""
    display_user_text = ""

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        user_text = (payload.get('message') or '').strip()
        display_user_text = user_text
    else:
        user_text = (request.form.get('message') or '').strip()
        display_user_text = user_text

        uploaded = request.files.get('txt_file')
        if uploaded and uploaded.filename:
            filename = uploaded.filename
            if not filename.lower().endswith('.txt'):
                return jsonify({'error': 'Only .txt files are supported for upload.'}), 400

            try:
                raw_bytes = uploaded.read()
                file_text = raw_bytes.decode('utf-8', errors='replace')
            except Exception as e:
                return jsonify({'error': f'Could not read uploaded txt file: {e}'}), 400

            if len(file_text) > MAX_UPLOAD_CHARS:
                file_text = file_text[:MAX_UPLOAD_CHARS]

            if not user_text:
                user_text = f"Please analyze the uploaded txt file '{filename}'."

            user_text = (
                f"{user_text}\n\n"
                f"Uploaded txt file name: {filename}\n"
                f"Uploaded txt file content:\n"
                f"```text\n{file_text}\n```"
            )

            if display_user_text:
                display_user_text = f"{display_user_text} [Attached: {filename}]"
            else:
                display_user_text = f"[Attached txt: {filename}]"

    if not user_text:
        return jsonify({'error': 'Please enter a message or upload a .txt file.'}), 400

    request_id = str(uuid.uuid4())
    with request_states_lock:
        request_states[request_id] = {
            "status": "running",
            "progress": [],
            "reply": "",
            "error": "",
            "user_text": display_user_text or user_text,
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": "",
        }

    worker = threading.Thread(target=_run_chat_request, args=(request_id, user_text), daemon=True)
    worker.start()

    return jsonify({'request_id': request_id, 'status': 'running'})


@app.route('/chat_status/<request_id>', methods=['GET'])
def chat_status(request_id: str):
    with request_states_lock:
        state = request_states.get(request_id)
        if not state:
            return jsonify({'error': 'Request not found.'}), 404
        return jsonify(state)


@app.route('/chat_history', methods=['GET'])
def chat_history():
    return jsonify({"history": _read_history()})


@app.route('/tool_schema/<name>', methods=['GET'])
def tool_schema(name: str):
    """Return a tool's JSON parameter schema so the flow panel can build an edit form."""
    return jsonify({"name": name, "schema": get_tool_param_schema(name)})


@app.route('/update_script_step', methods=['POST'])
def update_script_step():
    """Patch one node's arguments (and a condition node's condition) in the pending script.

    Editing is only allowed while a plan awaits confirmation; once the user runs it
    (or cancels), there is no pending script and this returns 409.
    """
    payload = request.get_json(silent=True) or {}
    node_id = payload.get("node_id")
    new_args = payload.get("arguments")
    condition = payload.get("condition")
    try:
        rounds = int(payload.get("rounds", 1) or 1)
    except (TypeError, ValueError):
        rounds = 1

    if not node_id or not isinstance(new_args, dict):
        return jsonify({"error": "node_id and arguments are required."}), 400

    session_key = id(messages)
    plan = PENDING_TEST_SCRIPTS.get(session_key)
    if not plan:
        return jsonify({"error": "No pending test plan to edit (it is already running or was cleared)."}), 409

    script = plan.get("script") or {}
    node, kind = resolve_node(script, node_id)
    if not node:
        return jsonify({"error": f"Step '{node_id}' was not found in the current script."}), 404

    target = node["if"] if kind == "condition" else node
    fn = str(target.get("function", ""))
    schema = get_tool_param_schema(fn)
    ok, coerced, errors = coerce_and_validate(schema, new_args)
    if not ok:
        return jsonify({"error": "; ".join(errors)}), 400

    target["arguments"] = coerced
    if kind == "condition" and isinstance(condition, str) and condition.strip():
        target["condition"] = condition.strip()

    # Keep the saved script .json in sync with the in-memory edit.
    path = plan.get("path")
    if path:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(script, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    model = build_flow_model(script, rounds, editable=True)
    return jsonify({"status": "ok", "flowchart": model})


@app.route('/active_request', methods=['GET'])
def active_request():
    """Expose an auto-resumed task so the UI can attach to it on page load."""
    with request_states_lock:
        rid = active_request_id
        state = request_states.get(rid) if rid else None
        if state:
            return jsonify({
                "request_id": rid,
                "user_text": state.get("user_text", ""),
                "status": state.get("status", "running"),
            })
    return jsonify({"request_id": None})


def _wait_for_system_ready(timeout: int = 600) -> None:
    """Wait until the GNAI endpoint responds before resuming.

    At logon the agent can launch before the network/VPN is up, so resuming
    immediately fails with a connection error. Poll the GNAI host until it
    answers (any HTTP response counts), with an upper time bound.
    """
    import time
    import httpx
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            resp = httpx.get(base_url, verify=False, timeout=10)
            print(f"[TASK RECOVERY] GNAI reachable (HTTP {resp.status_code}); proceeding with resume.")
            return
        except Exception as e:
            print(f"[TASK RECOVERY] Waiting for GNAI (attempt {attempt}): {e}")
            time.sleep(10)
    print("[TASK RECOVERY] GNAI readiness wait timed out; proceeding anyway.")

 

def _wait_for_system_ready(timeout: int = 600) -> None:
    """Wait until the GNAI endpoint responds before resuming.

    At logon the agent can launch before the network/VPN is up, so resuming
    immediately fails with a connection error. Poll the GNAI host until it
    answers (any HTTP response counts), with an upper time bound.
    """
    import time
    import httpx
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            resp = httpx.get(base_url, verify=False, timeout=10)
            print(f"[TASK RECOVERY] GNAI reachable (HTTP {resp.status_code}); proceeding with resume.")
            return
        except Exception as e:
            print(f"[TASK RECOVERY] Waiting for GNAI (attempt {attempt}): {e}")
            time.sleep(10)
    print("[TASK RECOVERY] GNAI readiness wait timed out; proceeding anyway.")


def _resume_pending_task_after_reboot() -> None:
    """If a test script was interrupted by a reboot step, resume it on startup.

    The runner saved a checkpoint (ai_task_state_manager.runner_state.json) describing the
    script, round, and next step. We continue it deterministically — no LLM — via
    run_test_script(resume_state=...), reusing the same report folder.
    """
    global active_request_id
    if not has_runner_state():
        return
    # Give the OS time to finish logon and bring up services/network first
    # (report generation + Echo analysis may need the network).
    _wait_for_system_ready()
    state = load_runner_state()
    if not state:
        return

    script = state.get("script") or {}
    rounds = int(state.get("rounds", 1) or 1)
    task = state.get("task", "test")

    request_id = str(uuid.uuid4())
    with request_states_lock:
        active_request_id = request_id
        request_states[request_id] = {
            "status": "running",
            "progress": [],
            "reply": "",
            "error": "",
            "user_text": f"[Auto-resume test after reboot] {task}",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": "",
        }

    def _step_logger(step_text: str) -> None:
        with request_states_lock:
            st = request_states.get(request_id)
            if st:
                st["progress"].append({
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                    "text": step_text,
                })

    def _worker() -> None:
        try:
            reply = run_test_script(
                script,
                tool_functions=ALL_TOOL_FUNCTIONS,
                rounds=rounds,
                step_callback=_step_logger,
                print_logs=True,
                script_dir=SCRIPT_DIR,
                report_generator=_generate_ai_report,
                resume_state=state,
            )
            with request_states_lock:
                st = request_states.get(request_id)
                if st:
                    st["status"] = "done"
                    st["reply"] = reply
                    st["ended_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    _append_history_item({
                        "request_id": request_id,
                        "status": "done",
                        "user_text": st.get("user_text", ""),
                        "started_at": st.get("started_at", ""),
                        "ended_at": st.get("ended_at", ""),
                        "progress": _strip_flow_events(st.get("progress", [])),
                        "reply": reply,
                    })
        except Exception as e:  # noqa: BLE001
            with request_states_lock:
                st = request_states.get(request_id)
                if st:
                    st["status"] = "error"
                    st["error"] = str(e)
                    st["ended_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    print("[TASK RECOVERY] Resuming interrupted test script from checkpoint...")


if __name__ == '__main__':
    import webbrowser, threading as _th

    def _open_browser():
        url = 'http://127.0.0.1:5000'
        try:
            if webbrowser.open(url):
                return
        except Exception:
            pass
        # Fallback for elevated/logon sessions where webbrowser may silently fail.
        try:
            os.startfile(url)
        except Exception:
            try:
                os.system(f'start "" "{url}"')
            except Exception:
                pass

    _th.Timer(1.2, _open_browser).start()
    # Resume an interrupted test script (if a reboot checkpoint exists) without
    # blocking server startup; the resume worker waits for the network itself.
    _th.Thread(target=_resume_pending_task_after_reboot, daemon=True).start()
    app.run(debug=False, host='127.0.0.1', port=5000)
