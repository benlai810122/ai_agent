from flask import Flask, render_template, request, jsonify
from ai_agent import _run_agent_turn
import threading
import uuid
import os
import json
from datetime import datetime


MAX_UPLOAD_CHARS = 50000

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Shared conversation state (per session in real app)
messages = []
lock = threading.Lock()
request_states = {}
request_states_lock = threading.Lock()
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report", "web_ui_run_history.json")
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


def _run_chat_request(request_id: str, user_text: str) -> None:
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
                    "progress": state.get("progress", []),
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
                    "progress": state.get("progress", []),
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

if __name__ == '__main__':
    import webbrowser, threading as _th
    _th.Timer(1.2, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(debug=False, host='127.0.0.1', port=5000)
