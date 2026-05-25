from flask import Flask, render_template, request, jsonify
from ai_agent import _run_agent_turn
import threading
import uuid
import os
import json
from datetime import datetime

app = Flask(__name__)

# Shared conversation state (per session in real app)
messages = []
lock = threading.Lock()
request_states = {}
request_states_lock = threading.Lock()
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report", "web_ui_run_history.json")
MAX_HISTORY_ITEMS = 100


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
    user_text = request.json.get('message', '')
    if not user_text:
        return jsonify({'error': 'Please enter a message.'}), 400

    request_id = str(uuid.uuid4())
    with request_states_lock:
        request_states[request_id] = {
            "status": "running",
            "progress": [],
            "reply": "",
            "error": "",
            "user_text": user_text,
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
