from flask import Flask, render_template, request, jsonify
from ai_agent import _run_agent_turn
import threading

app = Flask(__name__)

# Shared conversation state (per session in real app)
messages = []
lock = threading.Lock()

@app.route('/')
def index():
    return render_template('chat.html')

@app.route('/chat', methods=['POST'])
def chat():
    user_text = request.json.get('message', '')
    if not user_text:
        return jsonify({'reply': 'Please enter a message.'})
    with lock:
        reply = _run_agent_turn(messages, user_text, print_tool_logs=False)
    return jsonify({'reply': reply})

if __name__ == '__main__':
    app.run(debug=True)
