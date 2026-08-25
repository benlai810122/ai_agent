"""Persistent checkpoint for the deterministic test-script runner.

When a test script contains a `reboot_laptop` step, the runner process is killed
by the OS. Before issuing the reboot, `run_test_script` writes a checkpoint here
describing exactly where to continue: which script, which round of how many, and
the next step index. On the next logon the app reloads this file and resumes the
run deterministically (no LLM involved).

This is separate from the old LLM `pending_tasks.json` (which drove the
conversational agent); this file drives the script runner.
"""

import os
import sys
import json
from datetime import datetime


def _base_dir() -> str:
    """Stable, writable base dir that survives a reboot.

    For a frozen build the checkpoint MUST live next to the executable — the
    PyInstaller _MEIPASS folder is recreated on every launch, so anything saved
    there would be lost across the reboot.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


RUNNER_STATE_DIR = os.path.join(_base_dir(), "test_assets", "task_state")
RUNNER_STATE_PATH = os.path.join(RUNNER_STATE_DIR, "runner_state.json")


def save_runner_state(state: dict) -> str:
    """Persist the runner checkpoint. Returns the file path."""
    os.makedirs(RUNNER_STATE_DIR, exist_ok=True)
    payload = dict(state)
    payload.setdefault("saved_at", datetime.now().isoformat())
    with open(RUNNER_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return RUNNER_STATE_PATH


def load_runner_state() -> dict | None:
    """Return the saved checkpoint, or None if there isn't one / it's unreadable."""
    if not os.path.exists(RUNNER_STATE_PATH):
        return None
    try:
        with open(RUNNER_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def has_runner_state() -> bool:
    """True if a resumable checkpoint exists."""
    return os.path.exists(RUNNER_STATE_PATH)


def clear_runner_state() -> None:
    """Delete the checkpoint once the run has fully completed (or is abandoned)."""
    try:
        if os.path.exists(RUNNER_STATE_PATH):
            os.remove(RUNNER_STATE_PATH)
    except OSError:
        pass
