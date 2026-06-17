import sys
import subprocess
import shutil
import os
import time
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, 'frozen', False):
    PROJECT_ROOT = sys._MEIPASS
else:
    PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))

CDE_EXE = r"C:\Program Files\Intel\WRT2\cde.exe"
WRT_LOG_ROOT = r"C:\OSData\SystemData\Temp\WRT2G\Log"
WRT_CODE_WHITE_LIST = ["7019", "6050", "failed"]


def _to_epoch(start_time) -> float:
    """Normalize a start_time into an epoch float.

    Accepts either an epoch number (int/float) or a date/time string such as
    "2026-06-12 14:57:51". Raises ValueError if it cannot be parsed.
    """
    if isinstance(start_time, (int, float)):
        return float(start_time)

    if isinstance(start_time, str):
        text = start_time.strip()
        # Try to parse a numeric epoch passed as a string first.
        try:
            return float(text)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue

    raise ValueError(
        f"Could not interpret start_time={start_time!r} as an epoch timestamp "
        "or a 'YYYY-MM-DD HH:MM:SS' date string."
    )



def check_wrt_installed() -> dict:
    """Check whether the WRT system (cde.exe) is installed on this laptop.

    Returns:
        dict with status and an 'installed' boolean. If cde.exe does not exist,
        the laptop does not have the WRT system installed.
    """
    try:
        installed = os.path.isfile(CDE_EXE)
        return {
            "status": "success",
            "installed": installed,
            "cde_path": CDE_EXE,
            "message": (
                "WRT system is installed."
                if installed
                else "WRT system is NOT installed on this laptop (cde.exe not found)."
            ),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def dump_wrt_log(wait_seconds: int = 60) -> dict:
    """Dump WRT logs by running 'cde.exe dump_collect'.

    Args:
        wait_seconds: Time in seconds to wait for the log dump to complete (default 60).

    Returns:
        dict with status and command output.
    """
    try:
        if not os.path.isfile(CDE_EXE):
            return {"status": "error", "error": f"cde.exe not found at: {CDE_EXE}"}

        result = subprocess.run(
            f'"{CDE_EXE}" dump_collect',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
        )

        # Wait for the log dump to finish.
        time.sleep(wait_seconds)

        if result.returncode != 0:
            return {
                "status": "error",
                "return_code": result.returncode,
                "stderr": result.stderr.strip(),
            }

        return {
            "status": "success",
            "return_code": result.returncode,
            "stdout": result.stdout.strip(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def copy_wrt_log_to_file(start_time, log_path: str = "") -> dict:
    """Copy all WRT log folders modified after start_time to a target path.

    Args:
        start_time: When the test/cycle began. Accepts an epoch timestamp (float)
            or a "YYYY-MM-DD HH:MM:SS" date string. Only log folders modified at or
            after this time are copied, so each cycle captures only its own logs.
        log_path: Destination path (relative to the project root) to copy logs into.

    Returns:
        dict with status and the list of copied folders.
    """
    try:
        try:
            start_epoch = _to_epoch(start_time)
        except ValueError as e:
            return {"status": "error", "error": str(e)}

        if start_epoch <= 0:
            return {
                "status": "error",
                "error": (
                    "start_time must be a real test/cycle start time, not 0. "
                    "Call get_current_time at the start of the cycle and pass its "
                    "'timestamp' value so only this cycle's WRT logs are copied."
                ),
            }

        if not os.path.isdir(WRT_LOG_ROOT):
            return {"status": "error", "error": f"WRT log root not found at: {WRT_LOG_ROOT}"}

        all_dirs = [
            os.path.join(WRT_LOG_ROOT, d)
            for d in os.listdir(WRT_LOG_ROOT)
            if os.path.isdir(os.path.join(WRT_LOG_ROOT, d))
        ]
        if not all_dirs:
            return {"status": "error", "error": "No log directories found to copy."}

        copied = []
        skipped = 0
        for src_dir in all_dirs:
            create_time = os.path.getmtime(src_dir)
            if create_time >= start_epoch:
                target_path = os.path.join(PROJECT_ROOT, log_path, os.path.basename(src_dir))
                shutil.copytree(src_dir, target_path)
                copied.append(target_path)
            else:
                skipped += 1

        return {
            "status": "success",
            "start_time_epoch": start_epoch,
            "copied_count": len(copied),
            "skipped_count": skipped,
            "copied_folders": copied,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def clear_all_log() -> dict:
    """Clear all WRT logs by running 'cde.exe clear_all'.

    Returns:
        dict with status and command output.
    """
    try:
        if not os.path.isfile(CDE_EXE):
            return {"status": "error", "error": f"cde.exe not found at: {CDE_EXE}"}

        result = subprocess.run(
            f'"{CDE_EXE}" clear_all',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=True,
        )

        if result.returncode != 0:
            return {
                "status": "error",
                "return_code": result.returncode,
                "stderr": result.stderr.strip(),
            }

        return {
            "status": "success",
            "return_code": result.returncode,
            "stdout": result.stdout.strip(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def wrt_error_code_filter(log_path: str, white_list: list = None) -> dict:
    """Filter WRT error codes from log folder names.

    Args:
        log_path: Path (relative to the project root) containing WRT log folders.
        white_list: List of WRT codes to ignore. Defaults to WRT_CODE_WHITE_LIST.

    Returns:
        dict with status and the list of detected WRT error codes.

    WRT folder name example:
        ['DESKTOP-LGJ88V6', '29-12-2025', '13-58-04', '929', '9', '6050', '0x0', '0x0', '0x0']
    """
    try:
        if white_list is None:
            white_list = WRT_CODE_WHITE_LIST

        log_root = os.path.join(PROJECT_ROOT, log_path)
        if not os.path.isdir(log_root):
            return {"status": "error", "error": f"Log path not found at: {log_root}"}

        all_dirs = [
            os.path.join(log_root, d)
            for d in os.listdir(log_root)
            if os.path.isdir(os.path.join(log_root, d))
        ]

        wrt_error_code = []
        for src_dir in all_dirs:
            folder_info = Path(src_dir).name.split("_")
            wrt_code = None
            for info in folder_info:
                if len(info) == 4 and info.isdigit():
                    wrt_code = info
                    break
            happened_time = ""
            if len(folder_info) >= 3:
                happened_time = f"{folder_info[1]}-{folder_info[2]}"
            if wrt_code and wrt_code not in white_list:
                wrt_error_code.append(
                    f"Detect WRT CODE: {wrt_code} , happened time:{happened_time}"
                )

        return {
            "status": "success",
            "error_count": len(wrt_error_code),
            "wrt_error_codes": wrt_error_code,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


WRT_ANTHROPIC_TOOLS = [
    {
        "name": "check_wrt_installed",
        "description": "Check whether the WRT system (cde.exe) is installed on this laptop. Returns installed=false if cde.exe is not found, meaning the laptop does not have the WRT system installed.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dump_wrt_log",
        "description": "Dump WRT (Wireless Reliability Tool) logs by running 'cde.exe dump_collect'. Waits for the dump to complete before returning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wait_seconds": {
                    "type": "integer",
                    "description": "Time in seconds to wait for the log dump to complete. Defaults to 60."
                }
            },
            "required": [],
        },
    },
    {
        "name": "copy_wrt_log_to_file",
        "description": "Copy WRT log folders created during the current test/cycle into a destination path (relative to the project root). Only folders modified at or after start_time are copied, so pass the cycle's real start time to avoid copying old logs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_time": {
                    "type": "number",
                    "description": "The test/cycle start time as an epoch timestamp (use the 'timestamp' value returned by get_current_time at the start of the cycle). A date string 'YYYY-MM-DD HH:MM:SS' is also accepted. Must NOT be 0; only folders modified at or after this time are copied."
                },
                "log_path": {
                    "type": "string",
                    "description": "Destination path (relative to the project root) to copy logs into."
                }
            },
            "required": ["start_time"],
        },
    },
    {
        "name": "clear_all_log",
        "description": "Clear all WRT logs by running 'cde.exe clear_all'.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "wrt_error_code_filter",
        "description": "Scan WRT log folder names under a given path and return any detected WRT error codes that are not in the white list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "log_path": {
                    "type": "string",
                    "description": "Path (relative to the project root) containing WRT log folders."
                },
                "white_list": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of WRT codes to ignore. Defaults to the built-in white list (7019, 6050, failed)."
                }
            },
            "required": ["log_path"],
        },
    },
]

WRT_TOOL_FUNCTIONS = {
    "check_wrt_installed": check_wrt_installed,
    "dump_wrt_log": dump_wrt_log,
    "copy_wrt_log_to_file": copy_wrt_log_to_file,
    "clear_all_log": clear_all_log,
    "wrt_error_code_filter": wrt_error_code_filter,
}
