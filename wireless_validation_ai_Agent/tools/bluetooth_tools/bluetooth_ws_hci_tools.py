import os
import re
import subprocess
import tempfile
import uuid
import ctypes

# Path to hcitool.exe utility
HCITOOL_EXE_PATH = os.path.join(
    os.path.dirname(__file__),
    "Utilities",
    "hcitool",
    "x64",
    "hcitool.exe",
)

HCITOOL_I2S_CLOCK_COMMAND = "8CFC022102"
I2S_CLOCK_BYTE_INDEX_1_BASED = 23


def is_admin() -> bool:
    """Check if the current process has administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_with_elevated_privileges(command: list, timeout: int = 120) -> dict:
    """Run command as administrator and capture stdout/stderr/exit code."""
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
        stdout_file = os.path.join(temp_dir, f"hcitool_stdout_{token}.txt")
        stderr_file = os.path.join(temp_dir, f"hcitool_stderr_{token}.txt")
        exit_file = os.path.join(temp_dir, f"hcitool_exit_{token}.txt")
        runner_script = os.path.join(temp_dir, f"hcitool_runner_{token}.ps1")

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
            timeout=timeout,
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
            "returncode": exit_code,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "stdout": "",
            "stderr": "",
        }


def run_hcitool_command(command: list, timeout: int = 120) -> dict:
    """Run hcitool command with admin privileges by default."""
    if is_admin():
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "success": True,
                "stdout": result.stdout or "",
                "stderr": result.stderr or "",
                "returncode": result.returncode,
                "admin_mode": "already_admin",
            }
        except Exception as exc:
            return {
                "success": False,
                "error": str(exc),
                "stdout": "",
                "stderr": "",
                "admin_mode": "already_admin",
            }

    elevated = run_with_elevated_privileges(command, timeout=timeout)
    elevated["admin_mode"] = "elevated_runas"
    return elevated


def check_bluetooth_i2s_clock_source_config() -> dict:
    """Check platform Bluetooth I2S clock source BIOS config.

    Runs: hcitool.exe -c 8CFC022102

    23rd byte definition:
    - 84: Bluetooth drive I2S clock for HFP
    - 87: Audio DSP drive I2S clock for HFP
    - Others: Not Expected Config
    """
    if not os.path.exists(HCITOOL_EXE_PATH):
        return {
            "status": "error",
            "error": f"hcitool.exe not found at {HCITOOL_EXE_PATH}",
            "command": f'"{HCITOOL_EXE_PATH}" -c {HCITOOL_I2S_CLOCK_COMMAND}',
            "config_status": "unknown",
            "byte_23": None,
        }

    cmd_result = run_hcitool_command([HCITOOL_EXE_PATH, "-c", HCITOOL_I2S_CLOCK_COMMAND], timeout=30)

    if not cmd_result.get("success"):
        return {
            "status": "error",
            "error": cmd_result.get("error", "hcitool execution fail."),
            "command": f'"{HCITOOL_EXE_PATH}" -c {HCITOOL_I2S_CLOCK_COMMAND}',
            "config_status": "unknown",
            "byte_23": None,
            "stdout": cmd_result.get("stdout", ""),
            "stderr": cmd_result.get("stderr", ""),
            "admin_mode": cmd_result.get("admin_mode", "unknown"),
        }

    stdout = (cmd_result.get("stdout") or "").strip()
    stderr = (cmd_result.get("stderr") or "").strip()

    if not stdout:
        return {
            "status": "error",
            "error": "hcitool execution fail.",
            "command": f'"{HCITOOL_EXE_PATH}" -c {HCITOOL_I2S_CLOCK_COMMAND}',
            "return_code": cmd_result.get("returncode"),
            "stdout": stdout,
            "stderr": stderr,
            "config_status": "unknown",
            "byte_23": None,
            "admin_mode": cmd_result.get("admin_mode", "unknown"),
        }

    tokens = re.findall(r"\b[0-9A-Fa-f]{2}\b", stdout)
    tokens = [t.upper() for t in tokens]

    if not tokens:
        return {
            "status": "error",
            "error": "hcitool execution fail.",
            "command": f'"{HCITOOL_EXE_PATH}" -c {HCITOOL_I2S_CLOCK_COMMAND}',
            "return_code": cmd_result.get("returncode"),
            "stdout": stdout,
            "stderr": stderr,
            "config_status": "unknown",
            "byte_23": None,
            "admin_mode": cmd_result.get("admin_mode", "unknown"),
        }

    if len(tokens) < I2S_CLOCK_BYTE_INDEX_1_BASED:
        return {
            "status": "error",
            "error": (
                "Unexpected hcitool output format: "
                f"expected at least {I2S_CLOCK_BYTE_INDEX_1_BASED} bytes, got {len(tokens)}"
            ),
            "command": f'"{HCITOOL_EXE_PATH}" -c {HCITOOL_I2S_CLOCK_COMMAND}',
            "return_code": cmd_result.get("returncode"),
            "stdout": stdout,
            "stderr": stderr,
            "config_status": "unknown",
            "byte_23": None,
            "response_bytes": tokens,
            "admin_mode": cmd_result.get("admin_mode", "unknown"),
        }

    byte_23 = tokens[I2S_CLOCK_BYTE_INDEX_1_BASED - 1]

    if byte_23 == "87":
        config_status = "correct"
        description = "Audio DSP drive I2S clock for HFP"
    elif byte_23 == "84":
        config_status = "error"
        description = "Bluetooth drive I2S clock for HFP"
    else:
        config_status = "unexpected"
        description = "Not Expected Config"

    return {
        "status": "success",
        "command": f'"{HCITOOL_EXE_PATH}" -c {HCITOOL_I2S_CLOCK_COMMAND}',
        "return_code": cmd_result.get("returncode"),
        "stdout": stdout,
        "stderr": stderr,
        "response_bytes": tokens,
        "byte_23": byte_23,
        "config_status": config_status,
        "description": description,
        "is_expected_config": byte_23 in ("84", "87"),
        "is_correct_config": byte_23 == "87",
        "admin_mode": cmd_result.get("admin_mode", "unknown"),
    }


# Map of function name -> callable
HCITOOL_TOOL_FUNCTIONS = {
    "check_bluetooth_i2s_clock_source_config": check_bluetooth_i2s_clock_source_config,
}

# Anthropic-compatible tool definitions
HCITOOL_ANTHROPIC_TOOLS = [
    {
        "name": "check_bluetooth_i2s_clock_source_config",
        "description": (
            "Run hcitool.exe -c 8CFC022102 to check Bluetooth I2S clock source BIOS config. "
            "Parses the 23rd byte: 87=correct (Audio DSP drives I2S), "
            "84=error (Bluetooth drives I2S), others=unexpected config."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }
]
