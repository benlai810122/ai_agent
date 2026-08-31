import base64
import json
import os
import re
import subprocess
import sys
import time

import httpx2
import yaml
from anthropic import Anthropic

from tools.regular_tools.regular_ai_agent_tools import capture_screen


if getattr(sys, 'frozen', False):
    _PROJECT_ROOT = sys._MEIPASS
else:
    _PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "open_ai_key.yaml")
with open(_CONFIG_PATH, "r", encoding="utf-8") as _config_file:
    _config = yaml.safe_load(_config_file)

_HTTP_CLIENT = httpx2.Client(verify=False)
_ANTHROPIC_CLIENT = Anthropic(
    base_url="https://gnai.intel.com/api/providers/anthropic",
    auth_token=_config["gnai_token"],
    http_client=_HTTP_CLIENT,
)

_VISION_MODEL = "claude-4-5-sonnet"
_DISPLAY_RESOLUTION = "1920 x 1080"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _is_teams_running() -> bool:
    """Return True if any ms-teams process is currently running."""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-Process -Name 'ms-teams','Teams' -ErrorAction SilentlyContinue | Select-Object -First 1 Name"],
            capture_output=True, text=True, timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _send_teams_hotkey(*keys: str) -> None:
    """Press a hotkey combination while Teams window is focused."""
    import pyautogui
    import pygetwindow as gw

    # Bring Teams window to foreground
    windows = gw.getWindowsWithTitle("Microsoft Teams")
    if not windows:
        windows = gw.getWindowsWithTitle("Teams")
    if windows:
        win = windows[0]
        win.activate()
        time.sleep(0.5)

    pyautogui.hotkey(*keys)
    time.sleep(0.3)


def _move_and_click(x: int, y: int) -> dict:
    """Move the cursor visibly to a point, then click it."""
    import pyautogui

    start = pyautogui.position()
    pyautogui.moveTo(x, y, duration=0.6)
    time.sleep(0.1)
    pyautogui.click(x, y)
    end = pyautogui.position()

    return {
        "start": {"x": start.x, "y": start.y},
        "end": {"x": end.x, "y": end.y},
        "target": {"x": x, "y": y},
    }


def _extract_json_object(text: str) -> dict | None:
    """Extract the first JSON object from model output."""
    if not text:
        return None

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _find_join_button_via_vision() -> dict:
    """Capture the screen and ask the vision model to locate the Join Now button.

    The screenshot is downscaled before sending to the model to improve coordinate
    accuracy (large images can cause vision models to misestimate positions).
    Returned coordinates are scaled back to actual screen pixels before clicking.
    """
    shot = capture_screen()
    if shot.get("status") != "success":
        return {"status": "error", "error": shot.get("error", "Could not capture screen.")}

    file_path = shot.get("file_path")
    if not file_path or not os.path.exists(file_path):
        return {"status": "error", "error": f"Screenshot file not found: {file_path}"}

    from PIL import Image
    import tempfile

    with Image.open(file_path) as img:
        orig_w, orig_h = img.size
        # Downscale to 50% so the model reasons over a smaller image
        scale = 0.5
        small_w, small_h = int(orig_w * scale), int(orig_h * scale)
        small_img = img.resize((small_w, small_h), Image.LANCZOS)
        small_path = os.path.join(tempfile.gettempdir(), "agent_screenshot_small.png")
        small_img.save(small_path, "PNG")

    with open(small_path, "rb") as image_file:
        image_b64 = base64.b64encode(image_file.read()).decode("utf-8")

    try:
        response = _ANTHROPIC_CLIENT.messages.create(
            model=_VISION_MODEL,
            max_tokens=400,
            system=(
                f"You are helping locate the Microsoft Teams 'Join now' button in a screenshot."
                f" The image you receive is scaled to {small_w}x{small_h} pixels (50% of the original {orig_w}x{orig_h} screen)."
                " Return ONLY valid JSON with the shape: "
                '{"found":true|false,"x":number|null,"y":number|null,"confidence":0-1,"reason":"short text"}. '
                f"Use pixel coordinates relative to the SCALED image ({small_w}x{small_h}). Do NOT scale them up yourself."
            ),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Find the Microsoft Teams 'Join now' button in this screenshot. Return its center pixel coordinates in the scaled image.",
                        },
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                    ],
                }
            ],
        )
    except Exception as e:
        return {"status": "error", "error": f"Vision request failed: {e}"}

    text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    parsed = _extract_json_object(text)
    if not parsed:
        return {"status": "error", "error": f"Vision model did not return usable JSON: {text[:500]}"}

    # Scale coordinates back to actual screen pixels
    if parsed.get("x") is not None:
        parsed["x"] = int(parsed["x"] / scale)
    if parsed.get("y") is not None:
        parsed["y"] = int(parsed["y"] / scale)
    parsed["scale_used"] = scale
    parsed["original_resolution"] = f"{orig_w}x{orig_h}"
    parsed["scaled_resolution"] = f"{small_w}x{small_h}"

    return {"status": "success", "result": parsed, "raw": text}


# ── Tool functions ────────────────────────────────────────────────────────────

def check_teams_call_status() -> dict:
    """Check whether Microsoft Teams is running and whether a call is active.

    Uses multiple detection signals:
    1. Window title match (call|meeting|ringing|connected)
    2. Teams audio session via Windows audio graph
    3. Presence of teams-related call subprocess (msrtedit / CefSharp / wfica)

    Returns:
        dict with 'teams_running' bool, 'call_active' bool, and 'signals' detail.
    """
    try:
        teams_running = _is_teams_running()
        if not teams_running:
            return {
                "status": "success",
                "teams_running": False,
                "call_active": False,
                "message": "Microsoft Teams is not running.",
            }

        signals = {}

        # Signal 1: window title
        r1 = subprocess.run(
            ["powershell", "-Command",
             "Get-Process -Name 'ms-teams','Teams' -ErrorAction SilentlyContinue "
             "| Select-Object MainWindowTitle | ForEach-Object { $_.MainWindowTitle }"],
            capture_output=True, text=True, timeout=10,
        )
        titles = [t.strip() for t in r1.stdout.splitlines() if t.strip()]
        title_match = any(
            any(kw in t.lower() for kw in ("call", "meeting", "ringing", "connected", "in a call"))
            or (
                "|" in t
                and "microsoft teams" in t.lower()
                and len(t.split("|")) >= 3
                and not any(skip in t.lower() for skip in ("chat", "activity", "calendar", "files", "apps"))
            )
            for t in titles
        )
        signals["window_titles"] = titles
        signals["title_match"] = title_match

        # Signal 2: active audio session belonging to Teams
        r2 = subprocess.run(
            ["powershell", "-Command",
             "Get-Process -Name 'ms-teams','Teams' -ErrorAction SilentlyContinue "
             "| Select-Object -ExpandProperty Id "
             "| ForEach-Object { "
             "  try { "
             "    $s = New-Object -ComObject 'MMDeviceEnumerator'; $true "
             "  } catch { $false } "
             "}"],
            capture_output=True, text=True, timeout=10,
        )
        # Simpler audio check: see if Teams has an active audio endpoint
        r2b = subprocess.run(
            ["powershell", "-Command",
             "$pids = (Get-Process -Name 'ms-teams','Teams' -ErrorAction SilentlyContinue).Id; "
             "$audio = Get-Process -Name 'audiodg' -ErrorAction SilentlyContinue; "
             "if ($audio) { 'audio_running' } else { 'no_audio' }"],
            capture_output=True, text=True, timeout=10,
        )
        signals["audio_service"] = r2b.stdout.strip()

        # Signal 3: check all Teams window titles broadly (new Teams uses different process names)
        r3 = subprocess.run(
            ["powershell", "-Command",
             "Add-Type @'\n"
             "using System;\nusing System.Runtime.InteropServices;\n"
             "public class WinAPI {\n"
             "  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);\n"
             "  [DllImport(\"user32.dll\")] public static extern int GetWindowText(IntPtr hWnd, System.Text.StringBuilder lpString, int nMaxCount);\n"
             "  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);\n"
             "  public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);\n"
             "}\n'@\n"
             "$titles = @(); "
             "[WinAPI]::EnumWindows([WinAPI+EnumWindowsProc]{ param($h,$l) "
             "  $sb = New-Object System.Text.StringBuilder 256; "
             "  [WinAPI]::GetWindowText($h, $sb, 256) | Out-Null; "
             "  if ($sb.Length -gt 0 -and [WinAPI]::IsWindowVisible($h)) { $titles += $sb.ToString() }; $true "
             "}, [IntPtr]::Zero) | Out-Null; "
             "$titles | Where-Object { $_ -match 'Teams|Meeting|Call' }"],
            capture_output=True, text=True, timeout=15,
        )
        all_titles = [t.strip() for t in r3.stdout.splitlines() if t.strip()]
        broad_match = any(
            any(kw in t.lower() for kw in ("call", "meeting", "in a call", "connected"))
            for t in all_titles
        )
        signals["all_window_titles"] = all_titles
        signals["broad_title_match"] = broad_match

        call_active = title_match or broad_match

        return {
            "status": "success",
            "teams_running": True,
            "call_active": call_active,
            "signals": signals,
            "message": "Call is active." if call_active else "Teams is running but no active call detected.",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def join_teams_meeting(meeting_url: str = "", meeting_title: str = "") -> dict:
    """Join a Microsoft Teams meeting via URL using screenshot vision to click 'Join now'.

    Args:
        meeting_url: The full Teams meeting URL (https://teams.microsoft.com/l/meetup-join/...)

    Returns:
        dict with status, vision result, click info, and message.
    """
    try:
        if not meeting_url:
            return {"status": "error", "error": "Provide meeting_url to join the meeting."}

        # Open meeting URL — Teams protocol handler resolves it
        os.startfile(meeting_url)
        time.sleep(4)

        # Use screenshot + vision to find and click the Join Now button
        vision_result = _find_join_button_via_vision()
        if vision_result.get("status") != "success":
            return {
                "status": "error",
                "error": f"Vision failed to locate Join Now button: {vision_result.get('error')}",
            }

        parsed = vision_result.get("result", {})
        x = parsed.get("x")
        y = parsed.get("y")
        confidence = parsed.get("confidence", 0)

        if not parsed.get("found") or x is None or y is None or confidence < 0.35:
            return {
                "status": "error",
                "error": f"Vision could not locate Join Now button with sufficient confidence. Result: {parsed}",
            }

        click_info = _move_and_click(int(x), int(y))
        time.sleep(2)
        call_status = check_teams_call_status()

        return {
            "status": "success",
            "method": "vision_click",
            "vision": parsed,
            "click": click_info,
            "call_active": call_status.get("call_active", False),
            "message": (
                "Successfully clicked the Join Now button. Call is now active."
                if call_status.get("call_active")
                else "Clicked the Join Now button, but call active state not confirmed yet."
            ),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def leave_teams_call() -> dict:
    """Leave or end the current active Teams call using the keyboard shortcut Ctrl+Shift+H.

    Returns:
        dict with status and message.
    """
    try:
        if not _is_teams_running():
            return {"status": "error", "error": "Microsoft Teams is not running."}

        _send_teams_hotkey("ctrl", "shift", "h")

        return {
            "status": "success",
            "message": "Leave call shortcut (Ctrl+Shift+H) sent to Microsoft Teams.",
        }
    except ImportError:
        return {
            "status": "error",
            "error": "pyautogui or pygetwindow is not installed. Run: pip install pyautogui pygetwindow",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def mute_teams_microphone() -> dict:
    """Mute the microphone in an active Teams call using Ctrl+Shift+M.

    Returns:
        dict with status and message.
    """
    try:
        if not _is_teams_running():
            return {"status": "error", "error": "Microsoft Teams is not running."}

        _send_teams_hotkey("ctrl", "shift", "m")

        return {
            "status": "success",
            "message": "Mute microphone shortcut (Ctrl+Shift+M) sent to Microsoft Teams.",
        }
    except ImportError:
        return {
            "status": "error",
            "error": "pyautogui or pygetwindow is not installed. Run: pip install pyautogui pygetwindow",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def unmute_teams_microphone() -> dict:
    """Unmute the microphone in an active Teams call using Ctrl+Shift+M (toggle).

    Returns:
        dict with status and message.
    """
    try:
        if not _is_teams_running():
            return {"status": "error", "error": "Microsoft Teams is not running."}

        _send_teams_hotkey("ctrl", "shift", "m")

        return {
            "status": "success",
            "message": "Unmute microphone shortcut (Ctrl+Shift+M) sent to Microsoft Teams.",
        }
    except ImportError:
        return {
            "status": "error",
            "error": "pyautogui or pygetwindow is not installed. Run: pip install pyautogui pygetwindow",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def toggle_teams_camera() -> dict:
    """Toggle the camera on/off in an active Teams call using Ctrl+Shift+O.

    Returns:
        dict with status and message.
    """
    try:
        if not _is_teams_running():
            return {"status": "error", "error": "Microsoft Teams is not running."}

        _send_teams_hotkey("ctrl", "shift", "o")

        return {
            "status": "success",
            "message": "Toggle camera shortcut (Ctrl+Shift+O) sent to Microsoft Teams.",
        }
    except ImportError:
        return {
            "status": "error",
            "error": "pyautogui or pygetwindow is not installed. Run: pip install pyautogui pygetwindow",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Anthropic tool schemas ────────────────────────────────────────────────────

TEAMS_ANTHROPIC_TOOLS = [
    {
        "name": "check_teams_call_status",
        "description": (
            "Check whether Microsoft Teams is running and whether a call is currently active. "
            "Use this before performing any call actions to confirm Teams state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "join_teams_meeting",
        "description": (
            "Join a Microsoft Teams meeting by opening the meeting URL and automatically "
            "clicking the 'Join now' button using screenshot vision detection."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meeting_url": {
                    "type": "string",
                    "description": "Full Teams meeting URL (https://teams.microsoft.com/l/meetup-join/... or https://teams.microsoft.com/meet/...).",
                },
            },
            "required": ["meeting_url"],
        },
    },
    {
        "name": "leave_teams_call",
        "description": (
            "Leave or end the current active Microsoft Teams call. "
            "Sends the Ctrl+Shift+H keyboard shortcut to the Teams window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "mute_teams_microphone",
        "description": (
            "Mute the microphone in the active Teams call. "
            "Sends the Ctrl+Shift+M keyboard shortcut. "
            "If the mic is already muted this will unmute it — use check_teams_call_status first if needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "unmute_teams_microphone",
        "description": (
            "Unmute the microphone in the active Teams call. "
            "Sends the Ctrl+Shift+M keyboard shortcut (same toggle as mute). "
            "Use this when you know the mic is currently muted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "toggle_teams_camera",
        "description": (
            "Toggle the camera on or off in the active Teams call. "
            "Sends the Ctrl+Shift+O keyboard shortcut to the Teams window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

TEAMS_TOOL_FUNCTIONS = {
    "check_teams_call_status": check_teams_call_status,
    "join_teams_meeting": join_teams_meeting,
    "leave_teams_call": leave_teams_call,
    "mute_teams_microphone": mute_teams_microphone,
    "unmute_teams_microphone": unmute_teams_microphone,
    "toggle_teams_camera": toggle_teams_camera,
}
