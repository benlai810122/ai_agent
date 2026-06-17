import sys
import subprocess
import os


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    PROJECT_ROOT = sys._MEIPASS
else:
    PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
PWRTEST_EXE = os.path.join(PROJECT_ROOT, "Utilities", "pwrtest", "pwrtest.exe")


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


POWER_STATE_ANTHROPIC_TOOLS = [
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
]

POWER_STATE_TOOL_FUNCTIONS = {
    "go_to_s4": go_to_s4,
}
