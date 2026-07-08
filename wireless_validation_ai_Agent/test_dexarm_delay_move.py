"""
Test: queue a delayed DexArm move, then put the laptop to sleep immediately.

Flow:
    1. Connect + home the arm.
    2. Queue a G4 dwell (delay) and a move to the arm WITHOUT waiting
       (non-blocking), so the commands sit in the arm's own buffer.
    3. Immediately send the laptop into S3 sleep.

    Because the sends are non-blocking, Python does not wait out the delay --
    the arm executes the queued dwell + move on its own ~DELAY_SECONDS later,
    performing the physical wake-up action while the laptop is asleep.

Usage:
    python test_dexarm_delay_move.py
"""

import os
import sys
import time

# Make the dexarm tools importable regardless of where the script is run from.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from tools.dexarm_tools.dexarm_ai_agent_tools import (
    dexarm_connect,
    dexarm_disconnect,
    dexarm_go_home,
    dexarm_get_current_position,
    dexarm_move_to,
    dexarm_send_raw_gcode,
)
from tools.regular_tools.power_state_ai_agent_tools import go_to_s3

DELAY_SECONDS = 30        # delay to request before moving
MOVE_TARGET = {"x": -100, "y": 300, "z": 0}  # safe center-ish target; adjust as needed


def stamp(label, t0):
    """Print a label with elapsed time since t0."""
    print(f"[{time.time() - t0:6.2f}s] {label}")


def main():
    t0 = time.time()

    print("=== DexArm delay-then-move test ===")

    # 1. Connect
    res = dexarm_connect()
    stamp(f"dexarm_connect -> {res}", t0)
    if res.get("status") != "success":
        print("Could not connect to DexArm. Aborting.")
        return

    try:
        # 2. Home (required after power on)
        res = dexarm_go_home()
        stamp(f"dexarm_go_home -> {res.get('status')}", t0)

        # 3. Show starting position
        pos = dexarm_get_current_position()
        stamp(f"start position -> {pos.get('position')}", t0)

        # 4. QUEUE the delay + move to the arm WITHOUT blocking (wait=False).
        #    The commands are buffered by the DexArm and executed on its own
        #    queue, so Python returns right away and can sleep the laptop
        #    immediately. The arm will dwell DELAY_SECONDS and then move,
        #    performing the wake-up action while the laptop is in S3.
        print(f"\n--- queuing G4 S{DELAY_SECONDS} (delay) then move (non-blocking) ---")
        res = dexarm_send_raw_gcode(f"G4 S{DELAY_SECONDS}", wait=False)
        stamp(f"queued delay -> {res.get('status')}", t0)
        res = dexarm_move_to(wait=False, **MOVE_TARGET)
        stamp(f"queued move -> {res.get('status')}", t0)

        # 5. Put the laptop into S3 sleep IMMEDIATELY after queuing the commands.
        print("\n--- sending laptop to S3 sleep now ---")
        res = go_to_s3()
        stamp(f"go_to_s3 -> {res.get('status', res)}", t0)
        print(f"        {res.get('message', '')}")
        print(f"        The arm should move ~{DELAY_SECONDS}s from now and wake the laptop.")

    finally:
        res = dexarm_disconnect()
        stamp(f"dexarm_disconnect -> {res.get('status')}", t0)


if __name__ == "__main__":
    main()
