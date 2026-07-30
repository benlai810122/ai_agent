#!/usr/bin/env python3
"""Run the test script with delays directly."""

import os
import json
import sys

# Add the project root to the path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from ai_task_runner import run_test_script
from tools.regular_tools.regular_ai_agent_tools import TOOL_FUNCTIONS as REGULAR_TOOLS
from tools.bluetooth_tools.bluetooth_ai_agent_tools import BLUETOOTH_TOOL_FUNCTIONS
from tools.audio_headset_tools.headset_ai_agent_tools import HEADSET_TOOL_FUNCTIONS
from tools.mouse_keyboard_tools.mouse_ai_Agent_tools import MOUSE_KEYBOARD_TOOL_FUNCTIONS
from tools.regular_tools.power_state_ai_agent_tools import POWER_STATE_TOOL_FUNCTIONS
from tools.driver_install_tools.isst_driver_install_ai_agent_tools import ISST_DRIVER_INSTALL_TOOL_FUNCTIONS
from tools.bluetooth_tools.bluetooth_ws_hci_tools import HCITOOL_TOOL_FUNCTIONS
from tools.bluetooth_tools.bluetooth_ws_ibterverify_tools import IBTERVERIFY_TOOL_FUNCTIONS
from tools.arduino_tools.arduino_ai_agent_tools import ARDUINO_TOOL_FUNCTIONS
from tools.dexarm_tools.dexarm_ai_agent_tools import DEXARM_TOOL_FUNCTIONS
from tools.teams_tools.teams_ai_agent_tools import TEAMS_TOOL_FUNCTIONS
from tools.wrt_tools.wrt_ai_agent_tools import WRT_TOOL_FUNCTIONS

# Merge all tool functions
ALL_TOOLS = {}
ALL_TOOLS.update(REGULAR_TOOLS)
ALL_TOOLS.update(BLUETOOTH_TOOL_FUNCTIONS)
ALL_TOOLS.update(HEADSET_TOOL_FUNCTIONS)
ALL_TOOLS.update(MOUSE_KEYBOARD_TOOL_FUNCTIONS)
ALL_TOOLS.update(POWER_STATE_TOOL_FUNCTIONS)
ALL_TOOLS.update(ISST_DRIVER_INSTALL_TOOL_FUNCTIONS)
ALL_TOOLS.update(HCITOOL_TOOL_FUNCTIONS)
ALL_TOOLS.update(IBTERVERIFY_TOOL_FUNCTIONS)
ALL_TOOLS.update(ARDUINO_TOOL_FUNCTIONS)
ALL_TOOLS.update(DEXARM_TOOL_FUNCTIONS)
ALL_TOOLS.update(TEAMS_TOOL_FUNCTIONS)
ALL_TOOLS.update(WRT_TOOL_FUNCTIONS)

def main():
    # Load the test script
    script_path = os.path.join(SCRIPT_DIR, "test_scripts", "test_script_20260724_with_delays.json")
    
    if not os.path.exists(script_path):
        print(f"❌ Test script not found: {script_path}")
        return 1
    
    with open(script_path, 'r') as f:
        test_script = json.load(f)
    
    print("=" * 80)
    print(f"🧪 Running Test: {test_script.get('task', 'Unknown Task')}")
    print("=" * 80)
    print()
    
    # Run the test script
    result = run_test_script(
        test_script,
        tool_functions=ALL_TOOLS,
        rounds=1,
        step_callback=None,
        print_logs=True,
        write_report=True,
        script_dir=SCRIPT_DIR,
    )
    
    print()
    print("=" * 80)
    print(f"✅ Test Complete: {result}")
    print("=" * 80)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
