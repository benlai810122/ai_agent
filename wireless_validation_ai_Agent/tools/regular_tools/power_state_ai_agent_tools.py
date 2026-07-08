import sys
import subprocess
import os
import time
import json
from datetime import datetime
from pynput.keyboard import Controller, Key


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    PROJECT_ROOT = sys._MEIPASS
else:
    PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
PWRTEST_EXE = os.path.join(PROJECT_ROOT, "Utilities", "pwrtest", "pwrtest.exe")


def go_to_s3() -> dict:
    """
    Use keyboard shortcuts to put DUT into sleep mode (S3).
    Sends Win+X followed by U and S to trigger sleep dialog.
    Returns:
        dict with status and result.
    """
    try:
        keyboard = Controller()
        keyboard.press(Key.cmd)
        time.sleep(0.3)
        keyboard.press("x")
        time.sleep(0.3)
        keyboard.release("x")
        time.sleep(0.3)
        keyboard.release(Key.cmd)
        time.sleep(0.3)
        keyboard.tap("u")
        time.sleep(0.3)
        keyboard.tap("s")
        time.sleep(0.5)
        return {
            "status": "success",
            "message": "Sleep command sent. Laptop entering S3 sleep mode."
        }
    except Exception as e:
        return {"error": str(e)}


def power_state_s3_dexarm_wake(delay_seconds: int, x: float = -40, y: float = 220,
                               z: float = -58, z_diff: float = 8,
                               post_wake_settle_s: int = 5) -> dict:
    """Put the laptop into S3 sleep and wake it with a delayed DexArm double click.

    This runs the full power-state-s3 DexArm flow (Option 2) synchronously and
    BLOCKS until the laptop has woken and the arm has been returned home:
      1. Connect + home the DexArm and stage above the mouse target.
      2. Queue a delayed G4 dwell + double click NON-BLOCKING so the commands
         sit in the arm's buffer and the host is free to sleep immediately.
      3. Call go_to_s3() to put the laptop to sleep.
      4. The arm double-clicks after `delay_seconds`, waking the laptop; this
         function resumes, settles, homes the arm, and disconnects.

    Because the whole sequence runs in-process, the caller does not return until
    the laptop is awake again -- do NOT perform other steps until this returns.

    Args:
        delay_seconds: Seconds from now until the wake-up double click fires.
        x, y, z: Mouse-click target coordinates (z is the press depth).
        z_diff: Height to lift between press and release.
        post_wake_settle_s: Seconds to wait after wake before homing the arm.

    Returns:
        dict with status and details of the run.
    """
    from tools.dexarm_tools.dexarm_ai_agent_tools import (
        dexarm_connect,
        dexarm_disconnect,
        dexarm_go_home,
        dexarm_move_to,
        dexarm_send_raw_gcode,
    )
    try:
        conn = dexarm_connect()
        if conn.get("status") != "success":
            return {
                "status": "failure",
                "message": "Could not connect to DexArm; wake-up NOT armed. Laptop was NOT put to sleep.",
                "detail": conn,
            }
        try:
            dexarm_go_home()
            stage_z = z + z_diff
            # Stage above the target (blocking, so the arm is in position).
            dexarm_move_to(x=x, y=y, z=stage_z, wait=True)

            # Queue the delayed double click NON-BLOCKING.
            dexarm_send_raw_gcode(f"G4 S{delay_seconds}", wait=False)
            for _ in range(3):
                dexarm_move_to(x=x, y=y, z=z, wait=False)        # press
                dexarm_move_to(x=x, y=y, z=stage_z, wait=False)  # release

            # Put the laptop to sleep immediately. The process freezes here
            # while asleep and resumes after the arm's timed click wakes it.
            sleep_res = go_to_s3()

            # After wake, let the queued click finish, then home the arm.
            time.sleep(post_wake_settle_s)
            home_res = dexarm_go_home()

            return {
                "status": "success",
                "message": f"S3 wake via DexArm complete. Laptop slept and woke after ~{delay_seconds}s.",
                "delay_seconds": delay_seconds,
                "sleep_result": sleep_res,
                "home_result": home_res.get("status"),
            }
        finally:
            dexarm_disconnect()
    except Exception as e:
        return {"error": str(e)}



def go_to_s4(sleep_time: int = 60, cycles: int = 1, delay_time: int = 90) -> dict:
    """Put the laptop into S4 (hibernate) mode using pwrtest.exe.

    Args:
        sleep_time: Time in seconds to stay in S4 before waking up (default 60).
        cycles: Number of hibernate/resume cycles (default 1).
        delay_time: Delay in seconds before entering S4 (default 90).

    Returns:
        dict with status and command output.
    """
    try:
        if not os.path.isfile(PWRTEST_EXE):
            return {"error": f"pwrtest.exe not found at: {PWRTEST_EXE}"}

        cmd = [
            PWRTEST_EXE,
            "/sleep",
            f"/s:4",
            f"/p:{sleep_time}",
            f"/c:{cycles}",
            f"/d:{delay_time}",
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=os.path.dirname(PWRTEST_EXE),
        )

        stdout, stderr = proc.communicate(timeout=delay_time + sleep_time + 120)

        return {
            "status": "success" if proc.returncode == 0 else "error",
            "return_code": proc.returncode,
            "command": " ".join(cmd),
            "stdout": stdout.strip(),
            "stderr": stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "message": "pwrtest.exe did not return in time. The system may have hibernated and resumed successfully.",
            "command": " ".join(cmd),
        }
    except Exception as e:
        return {"error": str(e)}


def _save_task_state(tasks: dict) -> dict:
    """Save unfinished tasks to a JSON file for resuming after reboot.
    
    Args:
        tasks: Dictionary of unfinished tasks to persist.
        
    Returns:
        dict with status and file path.
    """
    try:
        task_state_dir = os.path.join(PROJECT_ROOT, "test_assets", "task_state")
        if not os.path.exists(task_state_dir):
            os.makedirs(task_state_dir)
        
        task_file = os.path.join(task_state_dir, "pending_tasks.json")
        
        state = {
            "saved_at": datetime.now().isoformat(),
            "tasks": tasks,
            "task_count": len(tasks)
        }
        
        with open(task_file, 'w') as f:
            json.dump(state, f, indent=2)
        
        return {
            "status": "success",
            "message": f"Saved {len(tasks)} task(s) to {task_file}",
            "file": task_file,
            "task_count": len(tasks)
        }
    except Exception as e:
        return {"error": f"Failed to save task state: {str(e)}"}


def _load_task_state() -> dict:
    """Load unfinished tasks from persistent storage.
    
    Returns:
        dict with status, tasks, and metadata.
    """
    try:
        task_file = os.path.join(PROJECT_ROOT, "test_assets", "task_state", "pending_tasks.json")
        
        if not os.path.exists(task_file):
            return {
                "status": "no_tasks",
                "message": "No pending tasks found",
                "tasks": {}
            }
        
        with open(task_file, 'r') as f:
            state = json.load(f)
        
        return {
            "status": "success",
            "message": f"Loaded {state.get('task_count', 0)} pending task(s)",
            "tasks": state.get("tasks", {}),
            "saved_at": state.get("saved_at"),
            "task_count": state.get("task_count", 0)
        }
    except Exception as e:
        return {"error": f"Failed to load task state: {str(e)}", "tasks": {}}


def _clear_task_state() -> dict:
    """Delete the persisted pending-task file once all tasks are finished.

    Returns:
        dict with status and message.
    """
    try:
        task_file = os.path.join(PROJECT_ROOT, "test_assets", "task_state", "pending_tasks.json")
        if os.path.exists(task_file):
            os.remove(task_file)
            return {"status": "success", "message": "Cleared pending task state"}
        return {"status": "no_tasks", "message": "No pending task file to clear"}
    except Exception as e:
        return {"error": f"Failed to clear task state: {str(e)}"}


def schedule_ai_agent_on_startup() -> dict:
    """Register the AI agent to run automatically on Windows startup using Task Scheduler.
    Launches the Launch Agent.bat batch file on user logon.
    
    Returns:
        dict with status and task creation result.
    """
    try:
        # Get the path to the Launch Agent batch file
        batch_file = os.path.join(PROJECT_ROOT, "Launch Agent.bat")
        
        if not os.path.exists(batch_file):
            return {"error": f"Launch Agent.bat not found at: {batch_file}"}
        
        # Task name for Windows Task Scheduler
        task_name = "WirelessValidationAIAgent"
        
        # Create task using schtasks command
        # /create: Create new task
        # /tn: Task name
        # /tr: Task to run (command)
        # /sc: Schedule (ONLOGON = when user logs in)
        # /rl: Run with privileges (HIGHEST for admin tasks)
        # /f: Force create (overwrite if exists)
        
        cmd = [
            "schtasks",
            "/create",
            f"/tn", task_name,
            f"/tr", f'"{batch_file}"',
            "/sc", "ONLOGON",
            "/rl", "HIGHEST",
            "/f"
        ]
        
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True
        )
        
        if proc.returncode == 0:
            return {
                "status": "success",
                "message": f"Task '{task_name}' scheduled to run on user logon",
                "task_name": task_name,
                "batch_file": batch_file,
                "command": " ".join(cmd)
            }
        else:
            return {
                "status": "error",
                "message": f"Failed to create task: {proc.stderr}",
                "return_code": proc.returncode
            }
    except Exception as e:
        return {"error": f"Exception scheduling task: {str(e)}"}


def reboot_laptop(delay_seconds: int = 20, save_tasks: bool = True, pending_tasks: dict = None) -> dict:
    """Reboot the laptop with optional task state persistence.
    
    Args:
        delay_seconds: Seconds to wait before rebooting (default 20).
        save_tasks: Whether to save pending tasks before reboot (default True).
        pending_tasks: Dictionary of pending tasks to save (optional).
        
    Returns:
        dict with status and reboot command info.
    """
    try:
        # Save tasks if provided
        if save_tasks and pending_tasks:
            save_result = _save_task_state(pending_tasks)
            if "error" in save_result:
                return {
                    "status": "warning",
                    "message": "Failed to save tasks but proceeding with reboot",
                    "error": save_result.get("error")
                }
        
        # Execute Windows shutdown command with reboot flag
        cmd = f"shutdown /r /t {delay_seconds} /c \"AI Agent initiated system reboot - resuming validation tasks\""
        
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True
        )
        
        if proc.returncode == 0:
            return {
                "status": "success",
                "message": f"System will reboot in {delay_seconds} seconds",
                "delay_seconds": delay_seconds,
                "tasks_saved": save_tasks and pending_tasks is not None,
                "task_count": len(pending_tasks) if pending_tasks else 0,
                "command": cmd
            }
        else:
            return {
                "status": "error",
                "message": f"Failed to initiate reboot: {proc.stderr}",
                "return_code": proc.returncode
            }
    except Exception as e:
        return {"error": f"Exception during reboot: {str(e)}"}


POWER_STATE_ANTHROPIC_TOOLS = [
    {
        "name": "go_to_s3",
        "description": "Put the laptop into S3 sleep mode using keyboard shortcuts (Win+X, U, S).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "power_state_s3_dexarm_wake",
        "description": "Put the laptop into S3 sleep and wake it after a delay using a physical DexArm double click. Use this when the DexArm is the wake-up device. This runs the ENTIRE flow synchronously and BLOCKS until the laptop has woken and the arm is homed, so you MUST wait for it to return before doing anything else. Do not call go_to_s3 or any DexArm tools separately for S3 wake-up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "delay_seconds": {
                    "type": "integer",
                    "description": "Seconds from now until the wake-up double click fires (time until the desired wake-up moment)."
                },
                "x": {"type": "number", "description": "Mouse-click target X coordinate. Defaults to -40."},
                "y": {"type": "number", "description": "Mouse-click target Y coordinate. Defaults to 220."},
                "z": {"type": "number", "description": "Press depth Z coordinate. Defaults to -58."},
                "z_diff": {"type": "number", "description": "Height to lift between press and release. Defaults to 8."}
            },
            "required": ["delay_seconds"],
        },
    },
    {
        "name": "go_to_s4",
        "description": "Put the laptop into S4 (hibernate) mode using pwrtest.exe. The system will hibernate for the specified sleep time and then wake up automatically. Requires administrator privileges.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sleep_time": {
                    "type": "integer",
                    "description": "Time in seconds to stay in S4 (hibernate) before waking up. Defaults to 60."
                },
                "cycles": {
                    "type": "integer",
                    "description": "Number of hibernate/resume cycles to perform. Defaults to 1."
                },
                "delay_time": {
                    "type": "integer",
                    "description": "Delay in seconds before entering S4. Defaults to 90."
                }
            },
            "required": [],
        },
    },
    {
        "name": "reboot_laptop",
        "description": "Reboot the laptop. The system will save pending tasks before rebooting and resume them after startup. The AI agent will be automatically executed on user logon.",
        "input_schema": {
            "type": "object",
            "properties": {
                "delay_seconds": {
                    "type": "integer",
                    "description": "Seconds to wait before rebooting. Defaults to 20. Minimum is 0."
                },
                "save_tasks": {
                    "type": "boolean",
                    "description": "Whether to save pending tasks for resumption after reboot. Defaults to True."
                }
            },
            "required": [],
        },
    },
    {
        "name": "schedule_ai_agent_on_startup",
        "description": "Register the AI agent to run automatically when the user logs in to Windows. This ensures the AI agent continues validation tasks after system restart or reboot.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

POWER_STATE_TOOL_FUNCTIONS = {
    "go_to_s3": go_to_s3,
    "power_state_s3_dexarm_wake": power_state_s3_dexarm_wake,
    "go_to_s4": go_to_s4,
    "reboot_laptop": reboot_laptop,
    "schedule_ai_agent_on_startup": schedule_ai_agent_on_startup,
}
