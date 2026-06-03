import subprocess
import os
import json
import time
import ctypes
import sys
import tempfile
import uuid

# Path to ibterverify.exe utility
IBTERVERIFY_EXE_PATH = os.path.join(
    os.path.dirname(__file__),
    "Utilities",
    "ibterverify",
    "ibterverify.exe"
)


def is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_with_elevated_privileges(command: list, timeout: int = 120) -> dict:
    """Run a command with elevated (administrator) privileges using PowerShell.
    
    Args:
        command: List of command and arguments (e.g., ['ibterverify.exe', '-p'])
        timeout: Timeout in seconds for the command execution
    
    Returns:
        Dictionary with 'stdout', 'stderr', 'returncode', and 'success' keys
    """
    try:
        if not command:
            return {
                "success": False,
                "error": "Empty command provided",
                "stdout": "",
                "stderr": "",
            }

        temp_dir = tempfile.gettempdir()
        token = uuid.uuid4().hex
        stdout_file = os.path.join(temp_dir, f"ibterverify_stdout_{token}.txt")
        stderr_file = os.path.join(temp_dir, f"ibterverify_stderr_{token}.txt")
        exit_file = os.path.join(temp_dir, f"ibterverify_exit_{token}.txt")
        runner_script = os.path.join(temp_dir, f"ibterverify_runner_{token}.ps1")

        def _ps_quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        args = command[1:] if len(command) > 1 else []
        ps_args = ", ".join(_ps_quote(str(arg)) for arg in args)

        runner_content = (
            "$ErrorActionPreference = 'Stop'\n"
            f"$exe = {_ps_quote(command[0])}\n"
            f"$stdoutFile = {_ps_quote(stdout_file)}\n"
            f"$stderrFile = {_ps_quote(stderr_file)}\n"
            f"$exitFile = {_ps_quote(exit_file)}\n"
            f"$args = @({ps_args})\n"
            "$proc = Start-Process -FilePath $exe -ArgumentList $args -RedirectStandardOutput $stdoutFile -RedirectStandardError $stderrFile -PassThru -Wait\n"
            "Set-Content -Path $exitFile -Value $proc.ExitCode -Encoding ASCII\n"
        )

        with open(runner_script, "w", encoding="utf-8") as f:
            f.write(runner_content)

        # Launch elevated PowerShell to run the script and wait for completion.
        launch_script = (
            "$ErrorActionPreference = 'Stop'; "
            "Start-Process -FilePath 'powershell' "
            f"-ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', {_ps_quote(runner_script)}) "
            "-Verb RunAs -PassThru -Wait | Out-Null"
        )

        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", launch_script],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "Elevated execution failed").strip()
            for p in (stdout_file, stderr_file, exit_file, runner_script):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
            return {
                "success": False,
                "error": error_text,
                "stdout": "",
                "stderr": result.stderr or "",
            }

        stdout = ""
        stderr = ""
        exit_code = 1

        if os.path.exists(stdout_file):
            with open(stdout_file, "r", encoding="utf-8", errors="ignore") as f:
                stdout = f.read()

        if os.path.exists(stderr_file):
            with open(stderr_file, "r", encoding="utf-8", errors="ignore") as f:
                stderr = f.read()

        if os.path.exists(exit_file):
            with open(exit_file, "r", encoding="utf-8", errors="ignore") as f:
                try:
                    exit_code = int(f.read().strip())
                except Exception:
                    exit_code = 1

        # Clean up temporary artifacts.
        for p in (stdout_file, stderr_file, exit_file, runner_script):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        
        return {
            "success": True,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": exit_code
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "stdout": "",
            "stderr": ""
        }


def run_command_with_fallback(command: list, timeout: int = 120) -> dict:
    """Run a command normally, and if it fails with permission error, try with elevated privileges.
    
    Args:
        command: List of command and arguments
        timeout: Timeout in seconds
        
    Returns:
        Dictionary with command execution results
    """
    # First try normal execution
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        
        # Check if we got permission denied error
        if result.returncode != 0 and ("Access denied" in stderr or "access denied" in stderr.lower() or "permission denied" in stderr.lower()):
            # Try with elevated privileges
            elevated = run_with_elevated_privileges(command, timeout)
            if elevated.get("success"):
                return elevated

        # WinError 740 can surface in stderr in some shells.
        if result.returncode != 0 and "740" in stderr:
            elevated = run_with_elevated_privileges(command, timeout)
            if elevated.get("success"):
                return elevated
        
        return {
            "success": True,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Command timed out after {timeout} seconds",
            "stdout": "",
            "stderr": ""
        }
    except PermissionError as e:
        # Try with elevated privileges
        elevated = run_with_elevated_privileges(command, timeout)
        if elevated.get("success"):
            return elevated
        return {
            "success": False,
            "error": str(e),
            "stdout": "",
            "stderr": ""
        }
    except OSError as e:
        # WinError 740: The requested operation requires elevation
        if getattr(e, "winerror", None) == 740:
            elevated = run_with_elevated_privileges(command, timeout)
            if elevated.get("success"):
                return elevated
        return {
            "success": False,
            "error": str(e),
            "stdout": "",
            "stderr": ""
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "stdout": "",
            "stderr": ""
        }


def check_bluetooth_pldr_support() -> dict:
    """Check if this system supports Bluetooth PLDR (Personal Local Device Recovery) using ibterverify.exe -p.
    
    This function always runs ibterverify with elevated privileges.
    """
    try:
        if not os.path.exists(IBTERVERIFY_EXE_PATH):
            return {
                "error": f"ibterverify.exe not found at {IBTERVERIFY_EXE_PATH}",
                "supported": False,
                "admin_required": True
            }

        # Always run ibterverify.exe -p with elevated privileges
        cmd_result = run_with_elevated_privileges([IBTERVERIFY_EXE_PATH, "-p"], timeout=30)
        
        if not cmd_result.get("success"):
            error_msg = cmd_result.get("error", "Unknown error")
            return {
                "error": f"Failed to execute ibterverify.exe: {error_msg}. Administrator privileges required.",
                "supported": False,
                "admin_required": True
            }

        stdout = cmd_result.get("stdout", "")
        stderr = cmd_result.get("stderr", "")
        return_code = cmd_result.get("returncode", 1)

        # Analyze output for support status
        support_indicators = [
            "PLDR" in stdout or "PLDR" in stderr,
            "supported" in stdout.lower() or "supported" in stderr.lower(),
            return_code == 0,
        ]

        is_supported = any(support_indicators) and "not" not in stdout.lower() and "not" not in stderr.lower()

        return {
            "status": "success",
            "supported": is_supported,
            "return_code": return_code,
            "stdout": stdout,
            "stderr": stderr,
            "admin_required": False,
            "description": "System supports Bluetooth PLDR" if is_supported else "System does not support Bluetooth PLDR"
        }
    except Exception as e:
        return {
            "error": f"Exception during check: {str(e)}",
            "supported": False,
            "admin_required": True
        }


def open_ui_windows() -> dict:
    """Open Bluetooth Settings and Device Manager UI windows for monitoring during test.
    
    Returns:
        Dictionary with process handles for later cleanup
    """
    processes = {}
    try:
        # Open Bluetooth Settings
        bt_settings_proc = subprocess.Popen(
            ["powershell", "-Command", "start ms-settings:bluetooth"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        processes["bt_settings"] = bt_settings_proc
        time.sleep(1)
        
        # Open Device Manager
        device_mgr_proc = subprocess.Popen(
            ["devmgmt.msc"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        processes["device_manager"] = device_mgr_proc
        time.sleep(1)
        
        return {
            "success": True,
            "processes": processes,
            "message": "Opened Bluetooth Settings and Device Manager"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "processes": processes
        }


def close_ui_windows(processes: dict) -> dict:
    """Close Bluetooth Settings and Device Manager UI windows.
    
    Args:
        processes: Dictionary of process handles from open_ui_windows()
    
    Returns:
        Dictionary with cleanup status
    """
    closed = []
    errors = []
    
    for name, proc in processes.items():
        try:
            if proc and proc.poll() is None:  # Check if process still running
                proc.terminate()
                # Give it a moment to terminate gracefully
                time.sleep(0.5)
                if proc.poll() is None:
                    proc.kill()
            closed.append(name)
        except Exception as e:
            errors.append(f"{name}: {str(e)}")
    
    return {
        "success": len(errors) == 0,
        "closed": closed,
        "errors": errors,
        "message": f"Closed {len(closed)} windows"
    }


def test_bluetooth_pldr() -> dict:
    """Test Bluetooth PLDR functionality using ibterverify.exe -p -v -t and analyze the generated log file.
    
    This function always runs ibterverify with elevated privileges.
    """
    ui_processes = {}
    try:
        if not os.path.exists(IBTERVERIFY_EXE_PATH):
            return {
                "error": f"ibterverify.exe not found at {IBTERVERIFY_EXE_PATH}",
                "test_completed": False,
                "admin_required": True
            }

        # Open UI windows for test monitoring
        ui_open_result = open_ui_windows()
        ui_processes = ui_open_result.get("processes", {})

        # Get current working directory for log file location
        log_file_path = os.path.join(os.getcwd(), "ibterverify-log.txt")

        # Remove old log file if it exists
        if os.path.exists(log_file_path):
            os.remove(log_file_path)

        # Always run ibterverify.exe -p -v -t with elevated privileges
        cmd_result = run_with_elevated_privileges([IBTERVERIFY_EXE_PATH, "-p", "-v", "-t"], timeout=120)
        
        if not cmd_result.get("success"):
            error_msg = cmd_result.get("error", "Unknown error")
            return {
                "error": f"Failed to execute ibterverify.exe test: {error_msg}. Administrator privileges required.",
                "test_completed": False,
                "admin_required": True
            }

        stdout = cmd_result.get("stdout", "")
        stderr = cmd_result.get("stderr", "")
        return_code = cmd_result.get("returncode", 1)

        # Wait a moment for log file to be written
        time.sleep(2)

        # Close UI windows after test completes
        close_result = close_ui_windows(ui_processes)

        # Read and parse log file
        log_content = ""
        log_analysis = {
            "log_exists": os.path.exists(log_file_path),
            "test_completed": False,
            "test_passed": False,
            "has_errors": False,
            "has_exceptions": False,
        }

        if os.path.exists(log_file_path):
            with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
                log_content = f.read()

            # Analyze log content for success/failure indicators
            log_analysis["test_completed"] = "PLDR Test completed successfully" in log_content
            log_analysis["test_passed"] = log_analysis["test_completed"]
            log_analysis["has_errors"] = "error" in log_content.lower()
            log_analysis["has_exceptions"] = "exception" in log_content.lower()

        # Check console output for success message
        console_success = "PLDR Test completed successfully" in stdout or "PLDR Test completed successfully" in stderr

        return {
            "status": "success",
            "test_completed": log_analysis["test_completed"] or console_success,
            "test_passed": log_analysis["test_passed"] or console_success,
            "has_errors": log_analysis["has_errors"],
            "has_exceptions": log_analysis["has_exceptions"],
            "return_code": return_code,
            "admin_required": False,
            "console_output": {
                "stdout": stdout,
                "stderr": stderr
            },
            "log_file": {
                "path": log_file_path,
                "exists": log_analysis["log_exists"],
                "content_preview": log_content[:500] if log_content else "No log file content"
            },
            "summary": generate_test_summary(log_analysis, console_success)
        }
    except Exception as e:
        # Ensure UI windows are closed even on exception
        try:
            if ui_processes:
                close_ui_windows(ui_processes)
        except Exception:
            pass
        return {
            "error": f"Exception during test: {str(e)}",
            "test_completed": False,
            "admin_required": True
        }


def read_ibterverify_log() -> dict:
    """Read and analyze the ibterverify-log.txt file."""
    try:
        log_file_path = os.path.join(os.getcwd(), "ibterverify-log.txt")

        if not os.path.exists(log_file_path):
            return {
                "status": "not_found",
                "message": f"Log file not found at {log_file_path}. Run test_bluetooth_pldr() first."
            }

        with open(log_file_path, "r", encoding="utf-8", errors="ignore") as f:
            log_content = f.read()

        # Analyze log content
        test_completed = "PLDR Test completed successfully" in log_content
        has_errors = "error" in log_content.lower()
        has_exceptions = "exception" in log_content.lower()

        # Extract test result section (last few lines typically contain result)
        lines = log_content.split("\n")
        result_section = "\n".join(lines[-20:]) if len(lines) > 20 else log_content

        return {
            "status": "success",
            "log_file": log_file_path,
            "test_completed": test_completed,
            "test_result": "PASSED" if test_completed and not has_errors else "FAILED",
            "has_errors": has_errors,
            "has_exceptions": has_exceptions,
            "full_content": log_content,
            "result_section": result_section,
            "line_count": len(lines)
        }
    except Exception as e:
        return {
            "error": str(e)
        }


def generate_test_summary(log_analysis: dict, console_success: bool) -> str:
    """Generate a human-readable summary of the test results."""
    if console_success or log_analysis.get("test_passed"):
        return "Bluetooth PLDR test completed successfully with no errors detected."
    elif log_analysis.get("has_exceptions") or log_analysis.get("has_errors"):
        return "Bluetooth PLDR test encountered errors or exceptions. Check log for details."
    elif log_analysis.get("test_completed"):
        return "Bluetooth PLDR test completed, but status unclear. Review log for details."
    else:
        return "Bluetooth PLDR test did not complete successfully."


# Map of function name -> callable
IBTERVERIFY_TOOL_FUNCTIONS = {
    "check_bluetooth_pldr_support": check_bluetooth_pldr_support,
    "test_bluetooth_pldr": test_bluetooth_pldr,
    "read_ibterverify_log": read_ibterverify_log,
}

# Anthropic-compatible tool definitions
IBTERVERIFY_ANTHROPIC_TOOLS = [
    {
        "name": "check_bluetooth_pldr_support",
        "description": "Check if the system supports Bluetooth PLDR (Personal Local Device Recovery) using ibterverify.exe -p.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "test_bluetooth_pldr",
        "description": "Test Bluetooth PLDR functionality using ibterverify.exe -p -v -t. Runs a comprehensive PLDR test and analyzes the generated ibterverify-log.txt file. Returns test status, errors, and log file location.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "read_ibterverify_log",
        "description": "Read and analyze the ibterverify-log.txt file generated by the PLDR test. Shows test result, errors, and exceptions.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]
