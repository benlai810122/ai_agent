import os
import subprocess
import platform
import webbrowser
import time
from urllib.parse import urlparse
from datetime import datetime


# Set ROOT_DIR to project root (up 2 levels from tools/regular_tools/)
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def delay(seconds: float) -> dict:
    """Pause execution for a specified number of seconds. Useful to add delays between test steps to allow systems to stabilize."""
    if not isinstance(seconds, (int, float)):
        return {"error": f"seconds must be a number, got {type(seconds).__name__}"}
    if seconds < 0:
        return {"error": "seconds must be non-negative"}
    if seconds > 300:
        return {"error": "seconds must not exceed 300 (5 minutes)"}
    
    time.sleep(seconds)
    return {
        "status": "success",
        "message": f"Paused for {seconds} second(s)",
        "seconds_delayed": seconds,
    }

def get_current_time() -> dict:
    """Get the current date and time on this computer."""
    now = datetime.now()
    return {
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
        "timestamp": now.timestamp(),
    }

def get_system_info() -> dict:
    """Get basic system information about this computer."""
    return {
        "system": platform.system(),
        "node_name": platform.node(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
    }

def get_laptop_info() -> dict:
    """Get detailed laptop information including OS version, hardware specs, disk, memory, network, and battery."""
    import psutil
    import socket
    import time

    info = {
        "computer_name": platform.node(),
        "os": platform.system(),
        "os_version": platform.version(),
        "os_release": platform.release(),
        "os_edition": platform.platform(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "cpu_cores_physical": psutil.cpu_count(logical=False),
        "cpu_cores_logical": psutil.cpu_count(logical=True),
        "cpu_frequency_mhz": round(psutil.cpu_freq().current) if psutil.cpu_freq() else "N/A",
        "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
        "ram_available_gb": round(psutil.virtual_memory().available / (1024**3), 2),
        "ram_usage_percent": psutil.virtual_memory().percent,
    }

    # Disk info
    disks = []
    for part in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "drive": part.device,
                "filesystem": part.fstype,
                "total_gb": round(usage.total / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
                "usage_percent": usage.percent,
            })
        except PermissionError:
            pass
    info["disks"] = disks

    # Network
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        info["hostname"] = hostname
        info["ip_address"] = ip
    except Exception:
        pass

    # Battery
    battery = psutil.sensors_battery()
    if battery:
        info["battery_percent"] = battery.percent
        info["battery_plugged_in"] = battery.power_plugged
        info["battery_time_left_min"] = round(battery.secsleft / 60) if battery.secsleft > 0 else "charging/full"

    # Uptime
    boot = psutil.boot_time()
    uptime_sec = time.time() - boot
    hours, rem = divmod(int(uptime_sec), 3600)
    minutes, _ = divmod(rem, 60)
    info["uptime"] = f"{hours}h {minutes}m"

    # Current user
    info["logged_in_user"] = os.getlogin()

    return info

def list_directory(path: str = ".") -> dict:
    """List files and folders in a directory."""
    try:
        entries = os.listdir(path)
        files = [e for e in entries if os.path.isfile(os.path.join(path, e))]
        folders = [e for e in entries if os.path.isdir(os.path.join(path, e))]
        return {"path": os.path.abspath(path), "files": files, "folders": folders}
    except Exception as e:
        return {"error": str(e)}

def run_shell_command(command: str) -> dict:
    """Run a shell command and return the output. Only use for safe, read-only commands."""
    blocked = ["rm ", "del ", "format ", "rmdir", "shutdown", "restart", "mkfs"]
    if any(b in command.lower() for b in blocked):
        return {"error": "This command is blocked for safety reasons."}
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=15
        )
        return {
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:500],
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 15 seconds."}
    except Exception as e:
        return {"error": str(e)}

def read_file_content(file_path: str) -> dict:
    """Read the content of a text file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read(5000)
        return {"file_path": os.path.abspath(file_path), "content": content}
    except Exception as e:
        return {"error": str(e)}


def create_file(file_path: str, content: str = "") -> dict:
    """Create a new text file with optional content. Fails if the file already exists."""
    try:
        if os.path.exists(file_path):
            return {"error": f"File already exists: {file_path}"}
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "success", "file_path": os.path.abspath(file_path)}
    except Exception as e:
        return {"error": str(e)}


def delete_file(file_path: str) -> dict:
    """Delete a text file."""
    try:
        if not os.path.exists(file_path):
            return {"error": f"File not found: {file_path}"}
        os.remove(file_path)
        return {"status": "success", "deleted": os.path.abspath(file_path)}
    except Exception as e:
        return {"error": str(e)}


def write_file(file_path: str, content: str) -> dict:
    """Write content to a text file, replacing any existing content."""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "success", "file_path": os.path.abspath(file_path)}
    except Exception as e:
        return {"error": str(e)}


def modify_file(file_path: str, old_text: str, new_text: str) -> dict:
    """Modify a text file by replacing a specific text occurrence with new text."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        if old_text not in content:
            return {"error": f"Text to replace not found in {file_path}"}
        content = content.replace(old_text, new_text, 1)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "success", "file_path": os.path.abspath(file_path)}
    except Exception as e:
        return {"error": str(e)}


def create_report_folder() -> dict:
    """Create a timestamped report folder under the project root."""
    try:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_root = os.path.join(ROOT_DIR, "report")
        report_dir = os.path.join(report_root, f"report_{stamp}")
        already_exists = os.path.isdir(report_dir)
        os.makedirs(report_dir, exist_ok=True)
        return {
            "status": "success",
            "folder_path": os.path.abspath(report_dir),
            "report_root": os.path.abspath(report_root),
            "created": not already_exists,
        }
    except Exception as e:
        return {"error": str(e)}


def capture_screen(save_path: str = "") -> dict:
    """Capture a screenshot of the laptop's screen for analysis. Returns the file path and resolution."""
    try:
        from PIL import ImageGrab
        import tempfile

        screenshot = ImageGrab.grab()

        if not save_path:
            save_path = os.path.join(tempfile.gettempdir(), "agent_screenshot.png")

        screenshot.save(save_path, "PNG")

        return {
            "status": "success",
            "file_path": os.path.abspath(save_path),
            "resolution": f"{screenshot.width}x{screenshot.height}",
        }
    except Exception as e:
        return {"error": str(e)}


def open_website(url: str) -> dict:
    """Open a website in the default browser using a validated HTTP/HTTPS URL."""
    try:
        if not isinstance(url, str) or not url.strip():
            return {"error": "url is required."}

        normalized_url = url.strip()
        if "://" not in normalized_url:
            normalized_url = f"https://{normalized_url}"

        parsed = urlparse(normalized_url)
        if parsed.scheme not in ("http", "https"):
            return {"error": "Only http and https URLs are allowed."}
        if not parsed.netloc:
            return {"error": "Invalid URL. Please provide a valid website address."}

        opened = webbrowser.open(normalized_url, new=2)
        return {
            "status": "success",
            "url": normalized_url,
            "opened": bool(opened),
            "message": "Website open request sent to default browser.",
        }
    except Exception as e:
        return {"error": str(e)}


def open_local_file(
    file_path: str,
    arguments: list[str] | None = None,
    capture_output: bool = True,
    timeout_seconds: int = 30,
) -> dict:
    """Open a local file under the project root folder.

    For .exe/.bat/.cmd files, optional arguments can be passed and process
    feedback (stdout/stderr/return code) can be captured.
    """
    try:
        if not isinstance(file_path, str) or not file_path.strip():
            return {"error": "file_path is required."}

        if arguments is None:
            arguments = []
        if not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
            return {"error": "arguments must be a list of strings."}

        try:
            timeout_seconds = int(timeout_seconds)
        except Exception:
            return {"error": "timeout_seconds must be an integer."}
        if timeout_seconds < 1 or timeout_seconds > 300:
            return {"error": "timeout_seconds must be between 1 and 300."}

        capture_output = bool(capture_output)

        requested_path = file_path.strip().strip('"').strip("'")
        if os.path.isabs(requested_path):
            candidate_path = os.path.abspath(requested_path)
        else:
            candidate_path = os.path.abspath(os.path.join(ROOT_DIR, requested_path))

        # Restrict access to files under the project root.
        if os.path.commonpath([candidate_path, ROOT_DIR]) != ROOT_DIR:
            return {"error": "Only files under the project root folder are allowed."}

        if not os.path.exists(candidate_path):
            return {"error": f"File not found: {candidate_path}"}
        if not os.path.isfile(candidate_path):
            return {"error": f"Path is not a file: {candidate_path}"}

        ext = os.path.splitext(candidate_path)[1].lower()
        executable_exts = {".exe", ".bat", ".cmd"}

        if ext in executable_exts:
            if platform.system() == "Windows":
                if ext in {".bat", ".cmd"}:
                    command = ["cmd.exe", "/c", candidate_path, *arguments]
                else:
                    command = [candidate_path, *arguments]
            else:
                command = [candidate_path, *arguments]

            if not capture_output:
                proc = subprocess.Popen(command, cwd=ROOT_DIR)
                launch_id = str(uuid.uuid4())[:8]
                with _OPENED_LOCAL_LOCK:
                    _OPENED_LOCAL_PROCESSES[launch_id] = {
                        "launch_id": launch_id,
                        "pid": proc.pid,
                        "file_path": candidate_path,
                        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }

                return {
                    "status": "success",
                    "file_path": candidate_path,
                    "launched_as_process": True,
                    "arguments": arguments,
                    "capture_output": False,
                    "pid": proc.pid,
                    "launch_id": launch_id,
                    "message": "Process started in background. Use close_local_file_process with launch_id to stop it.",
                }

            result = subprocess.run(
                command,
                capture_output=capture_output,
                text=True,
                timeout=timeout_seconds,
                cwd=ROOT_DIR,
            )

            return {
                "status": "success",
                "file_path": candidate_path,
                "launched_as_process": True,
                "arguments": arguments,
                "return_code": result.returncode,
                "stdout": (result.stdout or "")[:4000] if capture_output else "",
                "stderr": (result.stderr or "")[:2000] if capture_output else "",
            }

        if platform.system() == "Windows":
            os.startfile(candidate_path)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", candidate_path])
        else:
            subprocess.Popen(["xdg-open", candidate_path])

        return {
            "status": "success",
            "file_path": candidate_path,
            "launched_as_process": False,
            "message": "Open request sent to the OS default handler.",
        }
    except subprocess.TimeoutExpired:
        return {
            "error": f"Process timed out after {timeout_seconds} seconds.",
            "file_path": os.path.abspath(file_path),
        }
    except Exception as e:
        return {"error": str(e)}


def _kill_pid(pid: int, force: bool = False, timeout: float = 3.0) -> str:
    """Terminate a single process by PID using OS-native calls. Returns status string."""
    import signal

    if platform.system() == "Windows":
        # Use taskkill — fast, no psutil needed
        flag = "/F" if force else ""
        cmd = f"taskkill {flag} /PID {pid} /T".strip()
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        if result.returncode == 0:
            return "terminated"
        # Process may already be gone (exit code 128) or access denied
        if "not found" in (result.stderr or "").lower():
            return "already_stopped"
        # Fallback: force kill if graceful failed
        if not force:
            cmd_force = f"taskkill /F /PID {pid} /T"
            r2 = subprocess.run(cmd_force, shell=True, capture_output=True, text=True, timeout=timeout)
            return "terminated" if r2.returncode == 0 else f"failed: {r2.stderr.strip()[:200]}"
        return f"failed: {result.stderr.strip()[:200]}"
    else:
        try:
            os.kill(pid, signal.SIGTERM if not force else signal.SIGKILL)
            return "terminated"
        except ProcessLookupError:
            return "already_stopped"
        except PermissionError:
            return "access_denied"


def close_local_file_process(file_path: str = "", launch_id: str = "", force: bool = False) -> dict:
    """Close/terminate local process for media/executable files under the project root.

    Preferred: provide launch_id returned by open_local_file when capture_output is false.
    Alternative: provide file_path and this tool will use OS-native commands (taskkill/pkill)
    to find and stop matching processes without scanning all system processes.
    """
    try:
        # ── Path 1: tracked launch_id (fast, direct PID kill) ──
        if launch_id:
            with _OPENED_LOCAL_LOCK:
                launch = _OPENED_LOCAL_PROCESSES.get(launch_id)
            if not launch:
                return {"error": f"launch_id not found: {launch_id}"}

            pid = launch.get("pid")
            if not pid:
                return {"error": f"No pid found for launch_id: {launch_id}"}

            status = _kill_pid(pid, force=force)

            with _OPENED_LOCAL_LOCK:
                _OPENED_LOCAL_PROCESSES.pop(launch_id, None)

            return {
                "status": "success",
                "launch_id": launch_id,
                "pid": pid,
                "already_stopped": status == "already_stopped",
                "message": f"Process {status}.",
            }

        # ── Path 2: file_path matching via OS-native commands ──
        if not file_path or not file_path.strip():
            return {"error": "Provide either launch_id or file_path."}

        requested_path = file_path.strip().strip('"').strip("'")
        if os.path.isabs(requested_path):
            candidate_path = os.path.abspath(requested_path)
        else:
            candidate_path = os.path.abspath(os.path.join(ROOT_DIR, requested_path))

        if os.path.commonpath([candidate_path, ROOT_DIR]) != ROOT_DIR:
            return {"error": "Only files under the project root folder are allowed."}
        if not os.path.exists(candidate_path):
            return {"error": f"File not found: {candidate_path}"}

        # Also check tracked processes first (instant match by file_path)
        matched_from_tracked = []
        with _OPENED_LOCAL_LOCK:
            for lid, info in list(_OPENED_LOCAL_PROCESSES.items()):
                if info.get("file_path", "").lower() == candidate_path.lower():
                    matched_from_tracked.append((lid, info.get("pid")))

        tracked_results = []
        for lid, pid in matched_from_tracked:
            if pid:
                status = _kill_pid(pid, force=force)
                tracked_results.append({"pid": pid, "status": status})
            with _OPENED_LOCAL_LOCK:
                _OPENED_LOCAL_PROCESSES.pop(lid, None)

        # Use WMIC/tasklist + taskkill on Windows to find processes by image name
        target_basename = os.path.basename(candidate_path)
        os_results = []

        if platform.system() == "Windows":
            # Use wmic to find PIDs whose command line contains the file path (fast, no full scan)
            try:
                wmic_cmd = (
                    f'wmic process where "CommandLine like \'%%{target_basename}%%\'" '
                    f'get ProcessId /format:list'
                )
                result = subprocess.run(
                    wmic_cmd, shell=True, capture_output=True, text=True, timeout=5
                )
                pids_found = set()
                for line in (result.stdout or "").splitlines():
                    line = line.strip()
                    if line.startswith("ProcessId="):
                        try:
                            pid_val = int(line.split("=", 1)[1])
                            # Skip our own process
                            if pid_val != os.getpid() and pid_val > 4:
                                pids_found.add(pid_val)
                        except ValueError:
                            pass

                # Remove already-handled tracked PIDs
                already_handled = {p for _, p in matched_from_tracked}
                pids_found -= already_handled

                for pid_val in pids_found:
                    status = _kill_pid(pid_val, force=force)
                    os_results.append({"pid": pid_val, "status": status})
            except subprocess.TimeoutExpired:
                pass
        else:
            # On Linux/macOS, use pkill
            try:
                flag = "-9" if force else "-15"
                subprocess.run(
                    ["pkill", flag, "-f", target_basename],
                    capture_output=True, text=True, timeout=5,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        all_results = tracked_results + os_results
        terminated_pids = [r["pid"] for r in all_results if r["status"] == "terminated"]
        already_stopped = [r["pid"] for r in all_results if r["status"] == "already_stopped"]

        if not all_results:
            return {
                "status": "not_found",
                "file_path": candidate_path,
                "message": "No running process matched this file.",
            }

        return {
            "status": "success",
            "file_path": candidate_path,
            "terminated_pids": terminated_pids,
            "already_stopped_pids": already_stopped,
            "count": len(terminated_pids),
        }
    except Exception as e:
        return {"error": str(e)}


def close_media_player(force: bool = False) -> dict:
    """Close the 'Media Player' app by window title and known process names."""
    try:
        flag = "/F" if force else ""
        closed = []

        # 1. Try closing by window title "Media Player" (catches any variant)
        cmd_title = f'taskkill {flag} /FI "WINDOWTITLE eq Media Player" /T'.strip()
        try:
            result = subprocess.run(
                cmd_title, shell=True, capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                closed.append("Media Player (by window title)")
        except subprocess.TimeoutExpired:
            pass

        # 2. Also try by known process names as fallback
        for proc_name in ["Microsoft.Media.Player.exe", "wmplayer.exe"]:
            cmd = f"taskkill {flag} /IM {proc_name} /T".strip()
            try:
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=3,
                )
                combined = ((result.stdout or "") + (result.stderr or "")).lower()
                if result.returncode == 0:
                    closed.append(proc_name)
                elif "not found" not in combined and "no tasks" not in combined and not force:
                    cmd_f = f"taskkill /F /IM {proc_name} /T"
                    r2 = subprocess.run(
                        cmd_f, shell=True, capture_output=True, text=True, timeout=3,
                    )
                    if r2.returncode == 0:
                        closed.append(proc_name)
            except subprocess.TimeoutExpired:
                pass

        if closed:
            return {
                "status": "success",
                "closed": closed,
                "message": f"Closed {', '.join(closed)}.",
            }

        return {
            "status": "not_found",
            "message": "No Media Player is running.",
        }
    except Exception as e:
        return {"error": str(e)}


# ── Task scheduling (disabled) ────────────────────────────────────────────
# import threading
# import uuid
# 
# # Global task registry (disabled)
# # _SCHEDULED_TASKS = {}
# # _TASK_LOCK = threading.Lock()
# # _SCHEDULER_NOTIFIER = None

# Still needed for open_local_file:
import threading
import uuid
_OPENED_LOCAL_PROCESSES = {}
_OPENED_LOCAL_LOCK = threading.Lock()


# def set_scheduler_notifier(callback) -> dict:
#     """Register a callback that will be called when a scheduled task executes."""
#     global _SCHEDULER_NOTIFIER
#     try:
#         if callback is not None and not callable(callback):
#             return {"error": "callback must be callable or None."}
#         _SCHEDULER_NOTIFIER = callback
#         return {"status": "success"}
#     except Exception as e:
#         return {"error": str(e)}
#
#
# def _public_task_view(task: dict) -> dict:
#     """Return a JSON-safe copy of a task without runtime-only objects."""
#     return {
#         "task_id": task.get("task_id"),
#         "description": task.get("description"),
#         "status": task.get("status"),
#         "scheduled_at": task.get("scheduled_at"),
#         "execute_at": task.get("execute_at"),
#         "delay_minutes": task.get("delay_minutes"),
#         "executed_at": task.get("executed_at"),
#         "cancelled_at": task.get("cancelled_at"),
#     }
#
#
#
# Task scheduling functions (commented out - feature disabled):
# def schedule_task_in_minutes(task_description: str, delay_minutes: int) -> dict:
#     pass
#
# def list_scheduled_tasks() -> dict:
#     pass
#
# def cancel_scheduled_task(task_id: str) -> dict:
#     pass
#

def report_cycle_result(cycle_number: int, result: str, summary: str) -> dict:
    """Report the result of one completed iteration cycle during a multi-iteration test.

    Call this tool at the end of each iteration cycle so the user can see real-time
    per-cycle feedback instead of a flat list of step numbers that grows indefinitely.

    Args:
        cycle_number: The 1-based cycle number that just finished.
        result: Short outcome label, e.g. 'PASS', 'FAIL', or 'ERROR'.
        summary: A 1-2 sentence description of what happened in this cycle.

    Returns:
        Acknowledgement dict so the agent can continue to the next cycle.
    """
    return {
        "status": "acknowledged",
        "cycle_number": cycle_number,
        "result": result,
        "summary": summary,
    }


# Map of function name -> callable
TOOL_FUNCTIONS = {
    "delay": delay,
    "get_current_time": get_current_time,
    "get_system_info": get_system_info,
    "get_laptop_info": get_laptop_info,
    "list_directory": list_directory,
    "run_shell_command": run_shell_command,
    "read_file_content": read_file_content,
    "create_file": create_file,
    "delete_file": delete_file,
    "write_file": write_file,
    "modify_file": modify_file,
    "create_report_folder": create_report_folder,
    "capture_screen": capture_screen,
    "open_website": open_website,
    "open_local_file": open_local_file,
    "close_local_file_process": close_local_file_process,
    "close_media_player": close_media_player,
    # "schedule_task_in_minutes": schedule_task_in_minutes,
    # "list_scheduled_tasks": list_scheduled_tasks,
    # "cancel_scheduled_task": cancel_scheduled_task,
    "report_cycle_result": report_cycle_result,
}

TOOLS = [delay, get_current_time, get_system_info, get_laptop_info, list_directory, run_shell_command, read_file_content, create_file, delete_file, write_file, modify_file, create_report_folder, capture_screen, open_website, open_local_file, close_local_file_process, close_media_player]

# Anthropic-compatible tool definitions
ANTHROPIC_TOOLS = [
    {
        "name": "delay",
        "description": "Pause execution for a specified number of seconds. Use this to add delays between test steps to allow systems (like UI windows, file writes, or Bluetooth connections) to stabilize. Recommended between UI automation steps, after file I/O operations, or after hardware changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {
                    "type": "number",
                    "description": "Number of seconds to pause (float or int). Must be between 0 and 300 (5 minutes). Typical delays: 1-2 for UI window loading, 2-3 for Bluetooth state changes, 1-2 for file I/O operations."
                }
            },
            "required": ["seconds"],
        },
    },
    {
        "name": "get_current_time",
        "description": "Get the current date and time on this computer. Returns a human-readable 'datetime' string plus an epoch 'timestamp' (float). Use the 'timestamp' value when a tool requires an epoch start_time (e.g. copy_wrt_log_to_file).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_system_info",
        "description": "Get basic system information about this computer.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_laptop_info",
        "description": "Get detailed laptop information including OS version, hardware specs, disk, memory, network, and battery.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_directory",
        "description": "List files and folders in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The directory path to list. Defaults to current directory."}
            },
            "required": [],
        },
    },
    {
        "name": "run_shell_command",
        "description": "Run a shell command and return the output. Only use for safe, read-only commands.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to run."}
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file_content",
        "description": "Read the content of a text file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to read."}
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "create_file",
        "description": "Create a new text file with optional content. Fails if the file already exists.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path for the new file to create."},
                "content": {"type": "string", "description": "Optional initial content to write. Defaults to empty."}
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a text file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to delete."}
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a text file, replacing any existing content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to write."},
                "content": {"type": "string", "description": "The content to write to the file."}
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "modify_file",
        "description": "Modify a text file by replacing a specific text occurrence with new text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to modify."},
                "old_text": {"type": "string", "description": "The exact text to find and replace."},
                "new_text": {"type": "string", "description": "The new text to replace it with."}
            },
            "required": ["file_path", "old_text", "new_text"],
        },
    },
    {
        "name": "create_report_folder",
        "description": "Create the report folder if it does not exist.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "capture_screen",
        "description": "Capture a screenshot of the laptop's screen for analysis. Returns the file path and resolution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "save_path": {"type": "string", "description": "Optional path to save the screenshot. Defaults to temp directory."}
            },
            "required": [],
        },
    },
    {
        "name": "open_website",
        "description": "Open a website URL in the default web browser.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Website URL to open. If scheme is omitted, https is assumed."}
            },
            "required": ["url"],
        },
    },
    {
        "name": "open_local_file",
        "description": "Open a local file under the project root folder. For .exe/.bat/.cmd you can pass arguments and capture execution feedback.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Relative or absolute path to a file under the project root folder."},
                "arguments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional command-line arguments for .exe/.bat/.cmd files."
                },
                "capture_output": {
                    "type": "boolean",
                    "description": "When true, returns stdout/stderr/return_code for executable/script files. Defaults to true."
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Timeout in seconds for executable/script files. Range: 1-300. Defaults to 30."
                }
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "close_local_file_process",
        "description": "Close a running local media/app process under the project root. Prefer launch_id from open_local_file (when capture_output is false), or provide file_path for best-effort matching.",
        "input_schema": {
            "type": "object",
            "properties": {
                "launch_id": {"type": "string", "description": "Optional launch ID returned by open_local_file for background process launch."},
                "file_path": {"type": "string", "description": "Optional file path to match running processes by executable path, command line, or open file handles."},
                "force": {"type": "boolean", "description": "When true, forcibly kill matched processes instead of graceful terminate."}
            },
            "required": [],
        },
    },
    {
        "name": "close_media_player",
        "description": "Close Windows Media Player (wmplayer.exe or Microsoft.Media.Player.exe).",
        "input_schema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "description": "When true, forcibly kill the player instead of graceful terminate."}
            },
            "required": [],
        },
    },
    # {
    #     "name": "schedule_task_in_minutes",
    #     "description": "Schedule a task to be executed after a specified delay in minutes. Returns a task ID.",
    #     "input_schema": {
    #         "type": "object",
    #         "properties": {
    #             "task_description": {"type": "string", "description": "A description of the task to schedule."},
    #             "delay_minutes": {"type": "integer", "description": "Number of minutes to wait before executing. Must be at least 1."}
    #         },
    #         "required": ["task_description", "delay_minutes"],
    #     },
    # },
    # {
    #     "name": "list_scheduled_tasks",
    #     "description": "List all scheduled and executed tasks with their status and timing info.",
    #     "input_schema": {"type": "object", "properties": {}, "required": []},
    # },
    # {
    #     "name": "cancel_scheduled_task",
    #     "description": "Cancel a scheduled task before it executes using its task ID.",
    #     "input_schema": {
    #         "type": "object",
    #         "properties": {
    #             "task_id": {"type": "string", "description": "The ID of the scheduled task to cancel."}
    #         },
    #         "required": ["task_id"],
    #     },
    # },
    {
        "name": "report_cycle_result",
        "description": (
            "Call this tool at the end of EVERY iteration cycle during a multi-iteration test. "
            "It emits a clear cycle-complete marker so the user can track progress in real time "
            "and distinguish where one cycle ends and the next begins. "
            "Always call this before starting the next cycle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cycle_number": {
                    "type": "integer",
                    "description": "The 1-based number of the cycle that just finished.",
                },
                "result": {
                    "type": "string",
                    "description": "Cycle outcome: 'PASS', 'FAIL', or 'ERROR'.",
                },
                "summary": {
                    "type": "string",
                    "description": "A brief 1-2 sentence description of what happened in this cycle.",
                },
            },
            "required": ["cycle_number", "result", "summary"],
        },
    },
]

# OpenAI-compatible tool definitions (JSON schema format)
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current date and time on this computer. Returns a human-readable 'datetime' string plus an epoch 'timestamp' (float). Use the 'timestamp' value when a tool requires an epoch start_time (e.g. copy_wrt_log_to_file).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_system_info",
            "description": "Get basic system information about this computer.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_laptop_info",
            "description": "Get detailed laptop information including OS version, hardware specs, disk, memory, network, and battery.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and folders in a directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "The directory path to list. Defaults to current directory."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Run a shell command and return the output. Only use for safe, read-only commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run."}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file_content",
            "description": "Read the content of a text file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file to read."}
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new text file with optional content. Fails if the file already exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path for the new file to create."},
                    "content": {"type": "string", "description": "Optional initial content to write. Defaults to empty."}
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a text file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file to delete."}
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a text file, replacing any existing content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file to write."},
                    "content": {"type": "string", "description": "The content to write to the file."}
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_file",
            "description": "Modify a text file by replacing a specific text occurrence with new text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to the file to modify."},
                    "old_text": {"type": "string", "description": "The exact text to find and replace."},
                    "new_text": {"type": "string", "description": "The new text to replace it with."}
                },
                "required": ["file_path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_report_folder",
            "description": "Create the report folder if it does not exist.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_screen",
            "description": "Capture a screenshot of the laptop's screen for analysis. Returns the file path and resolution.",
            "parameters": {
                "type": "object",
                "properties": {
                    "save_path": {"type": "string", "description": "Optional path to save the screenshot. Defaults to temp directory."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Open a website URL in the default web browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Website URL to open. If scheme is omitted, https is assumed."}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_local_file",
            "description": "Open a local file under the project root folder. For .exe/.bat/.cmd you can pass arguments and capture execution feedback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Relative or absolute path to a file under the project root folder."},
                    "arguments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional command-line arguments for .exe/.bat/.cmd files."
                    },
                    "capture_output": {
                        "type": "boolean",
                        "description": "When true, returns stdout/stderr/return_code for executable/script files. Defaults to true."
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Timeout in seconds for executable/script files. Range: 1-300. Defaults to 30."
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_local_file_process",
            "description": "Close a running local media/app process under the project root. Prefer launch_id from open_local_file (when capture_output is false), or provide file_path for best-effort matching.",
            "parameters": {
                "type": "object",
                "properties": {
                    "launch_id": {"type": "string", "description": "Optional launch ID returned by open_local_file for background process launch."},
                    "file_path": {"type": "string", "description": "Optional file path to match running processes by executable path, command line, or open file handles."},
                    "force": {"type": "boolean", "description": "When true, forcibly kill matched processes instead of graceful terminate."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_media_player",
            "description": "Close Windows Media Player (wmplayer.exe or Microsoft.Media.Player.exe).",
            "parameters": {
                "type": "object",
                "properties": {
                    "force": {"type": "boolean", "description": "When true, forcibly kill the player instead of graceful terminate."}
                },
                "required": [],
            },
        },
    },
    # {
    #     "type": "function",
    #     "function": {
    #         "name": "schedule_task_in_minutes",
    #         "description": "Schedule a task to be executed after a specified delay in minutes. Returns a task ID.",
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "task_description": {"type": "string", "description": "A description of the task to schedule."},
    #                 "delay_minutes": {"type": "integer", "description": "Number of minutes to wait before executing. Must be at least 1."}
    #             },
    #             "required": ["task_description", "delay_minutes"],
    #         },
    #     },
    # },
    # {
    #     "type": "function",
    #     "function": {
    #         "name": "list_scheduled_tasks",
    #         "description": "List all scheduled and executed tasks with their status and timing info.",
    #         "parameters": {"type": "object", "properties": {}, "required": []},
    #     },
    # },
    # {
    #     "type": "function",
    #     "function": {
    #         "name": "cancel_scheduled_task",
    #         "description": "Cancel a scheduled task before it executes using its task ID.",
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "task_id": {"type": "string", "description": "The ID of the scheduled task to cancel."}
    #             },
    #             "required": ["task_id"],
    #         },
    #     },
    # },
]
