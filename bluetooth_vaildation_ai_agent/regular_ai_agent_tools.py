import os
import subprocess
import platform
from datetime import datetime


def get_current_time() -> dict:
    """Get the current date and time on this computer."""
    now = datetime.now()
    return {
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "day_of_week": now.strftime("%A"),
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
    """Create the report folder if it does not exist."""
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        report_dir = os.path.join(base_dir, "report")
        already_exists = os.path.isdir(report_dir)
        os.makedirs(report_dir, exist_ok=True)
        return {
            "status": "success",
            "folder_path": os.path.abspath(report_dir),
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


# ── Task scheduling ────────────────────────────────────────────
import threading
import uuid

# Global task registry
_SCHEDULED_TASKS = {}
_TASK_LOCK = threading.Lock()
_SCHEDULER_NOTIFIER = None


def set_scheduler_notifier(callback) -> dict:
    """Register a callback that will be called when a scheduled task executes."""
    global _SCHEDULER_NOTIFIER
    try:
        if callback is not None and not callable(callback):
            return {"error": "callback must be callable or None."}
        _SCHEDULER_NOTIFIER = callback
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}


def _public_task_view(task: dict) -> dict:
    """Return a JSON-safe copy of a task without runtime-only objects."""
    return {
        "task_id": task.get("task_id"),
        "description": task.get("description"),
        "status": task.get("status"),
        "scheduled_at": task.get("scheduled_at"),
        "execute_at": task.get("execute_at"),
        "delay_minutes": task.get("delay_minutes"),
        "executed_at": task.get("executed_at"),
        "cancelled_at": task.get("cancelled_at"),
    }


def schedule_task_in_minutes(task_description: str, delay_minutes: int) -> dict:
    """Schedule a task to be executed after a specified delay in minutes. Returns a task ID that can be used to track or cancel it."""
    try:
        if not task_description or not task_description.strip():
            return {"error": "task_description is required."}

        delay_minutes = int(delay_minutes)
        if delay_minutes < 1:
            return {"error": "delay_minutes must be at least 1 minute."}

        task_id = str(uuid.uuid4())[:8]
        scheduled_time = datetime.now()
        execute_time = scheduled_time
        delay_seconds = delay_minutes * 60

        # Create callback that updates task state and prints a visible reminder in terminal.
        def execute_task():
            task_for_notify = None
            with _TASK_LOCK:
                task = _SCHEDULED_TASKS.get(task_id)
                if not task or task.get("status") != "scheduled":
                    return
                task["status"] = "executed"
                task["executed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                task_for_notify = _public_task_view(task)

            # If agent registered a notifier, let it format a user-visible response.
            if _SCHEDULER_NOTIFIER and task_for_notify:
                try:
                    _SCHEDULER_NOTIFIER(task_for_notify)
                    return
                except Exception:
                    pass

            # Fallback reminder while the agent is waiting for user input.
            print(
                f"\n[SCHEDULED REMINDER] {task_description} (task_id={task_id}, time={datetime.now().strftime('%Y-%m-%d %H:%M:%S')})",
                flush=True,
            )

        # Store task info
        execute_at_text = datetime.fromtimestamp(execute_time.timestamp() + delay_seconds).strftime("%Y-%m-%d %H:%M:%S")
        with _TASK_LOCK:
            _SCHEDULED_TASKS[task_id] = {
                "task_id": task_id,
                "description": task_description.strip(),
                "status": "scheduled",
                "scheduled_at": scheduled_time.strftime("%Y-%m-%d %H:%M:%S"),
                "execute_at": execute_at_text,
                "delay_minutes": delay_minutes,
                "executed_at": None,
                "cancelled_at": None,
                "_timer": None,
            }

        # Schedule the timer thread
        timer = threading.Timer(delay_seconds, execute_task)
        timer.daemon = True
        with _TASK_LOCK:
            if task_id in _SCHEDULED_TASKS:
                _SCHEDULED_TASKS[task_id]["_timer"] = timer
        timer.start()

        return {
            "status": "scheduled",
            "task_id": task_id,
            "description": task_description.strip(),
            "delay_minutes": delay_minutes,
            "scheduled_at": scheduled_time.strftime("%Y-%m-%d %H:%M:%S"),
            "execute_at": execute_at_text,
            "message": f"Task '{task_description}' scheduled to run in {delay_minutes} minute(s). Task ID: {task_id}",
        }
    except Exception as e:
        return {"error": str(e)}


def list_scheduled_tasks() -> dict:
    """List all scheduled and executed tasks."""
    try:
        with _TASK_LOCK:
            tasks = [_public_task_view(t) for t in _SCHEDULED_TASKS.values()]

        scheduled = [t for t in tasks if t["status"] == "scheduled"]
        executed = [t for t in tasks if t["status"] == "executed"]
        cancelled = [t for t in tasks if t["status"] == "cancelled"]

        return {
            "status": "success",
            "total_tasks": len(tasks),
            "pending_count": len(scheduled),
            "executed_count": len(executed),
            "cancelled_count": len(cancelled),
            "pending_tasks": scheduled,
            "executed_tasks": executed,
            "cancelled_tasks": cancelled,
        }
    except Exception as e:
        return {"error": str(e)}


def cancel_scheduled_task(task_id: str) -> dict:
    """Cancel a scheduled task before it executes."""
    try:
        with _TASK_LOCK:
            if task_id not in _SCHEDULED_TASKS:
                return {"error": f"Task ID '{task_id}' not found."}

            task = _SCHEDULED_TASKS[task_id]
            if task["status"] != "scheduled":
                return {
                    "status": "error",
                    "message": f"Cannot cancel task '{task_id}'. Current status: {task['status']}.",
                }

            timer = task.get("_timer")
            if timer:
                timer.cancel()
            task["status"] = "cancelled"
            task["cancelled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return {
            "status": "success",
            "task_id": task_id,
            "message": f"Task '{task_id}' has been cancelled.",
        }
    except Exception as e:
        return {"error": str(e)}


# Map of function name -> callable
TOOL_FUNCTIONS = {
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
    "schedule_task_in_minutes": schedule_task_in_minutes,
    "list_scheduled_tasks": list_scheduled_tasks,
    "cancel_scheduled_task": cancel_scheduled_task,
}

TOOLS = [get_current_time, get_system_info, get_laptop_info, list_directory, run_shell_command, read_file_content, create_file, delete_file, write_file, modify_file, create_report_folder, capture_screen, schedule_task_in_minutes, list_scheduled_tasks, cancel_scheduled_task]

# Anthropic-compatible tool definitions
ANTHROPIC_TOOLS = [
    {
        "name": "get_current_time",
        "description": "Get the current date and time on this computer.",
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
        "name": "schedule_task_in_minutes",
        "description": "Schedule a task to be executed after a specified delay in minutes. Returns a task ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_description": {"type": "string", "description": "A description of the task to schedule."},
                "delay_minutes": {"type": "integer", "description": "Number of minutes to wait before executing. Must be at least 1."}
            },
            "required": ["task_description", "delay_minutes"],
        },
    },
    {
        "name": "list_scheduled_tasks",
        "description": "List all scheduled and executed tasks with their status and timing info.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "cancel_scheduled_task",
        "description": "Cancel a scheduled task before it executes using its task ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The ID of the scheduled task to cancel."}
            },
            "required": ["task_id"],
        },
    },
]

# OpenAI-compatible tool definitions (JSON schema format)
OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current date and time on this computer.",
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
            "name": "schedule_task_in_minutes",
            "description": "Schedule a task to be executed after a specified delay in minutes. Returns a task ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {"type": "string", "description": "A description of the task to schedule."},
                    "delay_minutes": {"type": "integer", "description": "Number of minutes to wait before executing. Must be at least 1."}
                },
                "required": ["task_description", "delay_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scheduled_tasks",
            "description": "List all scheduled and executed tasks with their status and timing info.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_scheduled_task",
            "description": "Cancel a scheduled task before it executes using its task ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The ID of the scheduled task to cancel."}
                },
                "required": ["task_id"],
            },
        },
    },
]
