"""ICS / ICPS (Intel Connectivity Performance Suite) AI-agent tools.

Automates the Intel Connection Suite UI and uses OCR to verify the Smart Connect
feature state. Adapted from the standalone ICS_SC_Check.py test script.
"""

import subprocess
import time
import ctypes
import ctypes.wintypes

# ── Config ─────────────────────────────────────────────────────────────────────
AUMID        = "AppUp.IntelConnectivityPerformanceSuite_8j3eq9eme6ctt!Intel.ICPS"
WINDOW_TITLE = "Intel® Connection Suite"
SIDEBAR_BUTTONS = ["Dashboard", "Smart Connect", "Link Sense", "Fast Lane", "Settings"]

TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

SC_STATE_UNAVAILABLE  = "UNAVAILABLE"
SC_STATE_NO_MY_NET    = "NO_MY_NETWORKS"
SC_STATE_NO_OTHER_NET = "NO_OTHER_NETWORKS"
SC_STATE_PASS         = "PASS"


def _configure_dpi_and_ocr():
    """Set process DPI awareness and point pytesseract at the Tesseract binary."""
    import pytesseract

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


# ── Window Helpers ─────────────────────────────────────────────────────────────
def _get_window_rect_physical(title):
    import win32gui

    hwnd = win32gui.FindWindow(None, title)
    if not hwnd:
        raise RuntimeError(f"Window not found: {title}")
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _win32_click(x, y):
    import win32api
    import win32con

    screen_w = win32api.GetSystemMetrics(0)
    screen_h = win32api.GetSystemMetrics(1)
    x = max(0, min(x, screen_w - 1))
    y = max(0, min(y, screen_h - 1))
    win32api.SetCursorPos((x, y))
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)


# ── Launch / Attach ────────────────────────────────────────────────────────────
def _launch_ics():
    subprocess.Popen(
        ["cmd", "/c", "start", "", f"shell:AppsFolder\\{AUMID}"],
        shell=False,
    )


def _attach_ics(timeout=30):
    from pywinauto import Application

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            app = Application(backend="uia").connect(title=WINDOW_TITLE, timeout=2)
            print("[OK] Attached to ICS.")
            return app
        except Exception:
            time.sleep(1)
    raise TimeoutError("ICS window did not appear in time.")


# ── OCR Helpers ────────────────────────────────────────────────────────────────
def _capture_sidebar(padding=10):
    from PIL import ImageGrab

    left, top, right, bottom = _get_window_rect_physical(WINDOW_TITLE)
    win_w = right - left
    sidebar_right = left + int(win_w * 0.15)
    region = (left - padding, top - padding, sidebar_right + padding, bottom + padding)
    return ImageGrab.grab(bbox=region), left, top, region


def _capture_content_area(debug=False):
    from PIL import ImageGrab

    left, top, right, bottom = _get_window_rect_physical(WINDOW_TITLE)
    win_w = right - left
    content_left  = left + int(win_w * 0.16)
    content_right = left + int(win_w * 0.65)
    region = (content_left, top, content_right, bottom)
    img = ImageGrab.grab(bbox=region)
    if debug:
        img.save("content_debug.png")
    return img, region


def _preprocess_image(img):
    img = img.convert("L")
    img = img.point(lambda p: 255 if p > 80 else 0)
    return img


def _ocr_all_text(img, region):
    import pytesseract

    processed = _preprocess_image(img)
    data = pytesseract.image_to_data(
        processed,
        output_type=pytesseract.Output.DICT,
        config="--psm 11",
    )
    results = []
    for i, word in enumerate(data["text"]):
        if not word.strip() or int(data["conf"][i]) < 30:
            continue
        x = region[0] + data["left"][i] + data["width"][i] // 2
        y = region[1] + data["top"][i]  + data["height"][i] // 2
        results.append({
            "text": word, "conf": int(data["conf"][i]), "x": x, "y": y,
            "top":    region[1] + data["top"][i],
            "bottom": region[1] + data["top"][i] + data["height"][i],
        })
    return results


def _find_text_ocr(search_text):
    import pytesseract

    img, win_left, win_top, region = _capture_sidebar()
    processed = _preprocess_image(img)
    data = pytesseract.image_to_data(
        processed,
        output_type=pytesseract.Output.DICT,
        config="--psm 11",
    )
    search_lower   = search_text.lower()
    search_nospace = search_lower.replace(" ", "")
    matches = []

    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        word_lower   = word.lower()
        word_nospace = word_lower.replace(" ", "")
        if search_lower in word_lower:
            x = region[0] + data["left"][i] + data["width"][i] // 2
            y = region[1] + data["top"][i]  + data["height"][i] // 2
            matches.append((int(data["conf"][i]), x, y, word))
        elif search_nospace in word_nospace:
            x = region[0] + data["left"][i] + data["width"][i] // 2
            y = region[1] + data["top"][i]  + data["height"][i] // 2
            matches.append((int(data["conf"][i]), x, y, word))

    if not matches:
        words_to_find = search_lower.split()
        if len(words_to_find) > 1:
            for i, word in enumerate(data["text"]):
                if words_to_find[0] in word.lower():
                    next_idx = i + 1
                    while next_idx < len(data["text"]) and not data["text"][next_idx].strip():
                        next_idx += 1
                    if next_idx < len(data["text"]) and words_to_find[1] in data["text"][next_idx].lower():
                        x1 = region[0] + data["left"][i]        + data["width"][i]        // 2
                        x2 = region[0] + data["left"][next_idx] + data["width"][next_idx] // 2
                        y1 = region[1] + data["top"][i]         + data["height"][i]        // 2
                        y2 = region[1] + data["top"][next_idx]  + data["height"][next_idx] // 2
                        matches.append((80, (x1+x2)//2, (y1+y2)//2, search_text))

    if matches:
        matches.sort(reverse=True)
        conf, x, y, word = matches[0]
        print(f"[OCR] Found '{search_text}' (matched '{word}', conf={conf}) at ({x}, {y})")
        return x, y

    print(f"[OCR] '{search_text}' not found in sidebar")
    return None


# ── Navigation ─────────────────────────────────────────────────────────────────
def _set_foreground_window_safe(title):
    import win32api
    import win32con
    import win32gui

    hwnd = win32gui.FindWindow(None, title)
    if not hwnd:
        raise RuntimeError(f"Window not found: {title}")
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        try:
            win32gui.ShowWindow(hwnd, 5)
            time.sleep(0.2)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            try:
                win32api.keybd_event(0x12, 0, 0, 0)
                win32gui.SetForegroundWindow(hwnd)
                win32api.keybd_event(0x12, 0, win32con.KEYEVENTF_KEYUP, 0)
            except Exception as e:
                print(f"[WARN] SetForegroundWindow all methods failed: {e}")
    time.sleep(0.3)


def _navigate_to(section: str, retries=3):
    _set_foreground_window_safe(WINDOW_TITLE)
    for attempt in range(1, retries + 1):
        result = _find_text_ocr(section)
        if result:
            x, y = result
            _win32_click(x, y)
            print(f"[NAV] Navigated to '{section}' (attempt {attempt})")
            time.sleep(1.5)
            return True
        print(f"[RETRY] Attempt {attempt}/{retries} failed for '{section}'")
        time.sleep(0.5)
    print(f"[FAIL] Could not navigate to '{section}'")
    return False


# ── Smart Connect Verification ─────────────────────────────────────────────────
def _get_section_items(all_words, section_header, next_headers):
    header_y = None
    for w in all_words:
        if section_header.lower() in w["text"].lower():
            header_y = w["y"]
            break
    if header_y is None:
        return None, []
    boundary_y = 99999
    for nxt in next_headers:
        for w in all_words:
            if nxt.lower() in w["text"].lower() and w["y"] > header_y:
                if w["y"] < boundary_y:
                    boundary_y = w["y"]
    items = [
        w["text"] for w in all_words
        if w["y"] > header_y and w["y"] < boundary_y
        and w["text"].strip()
        and w["text"] not in ["=", "@", "$", "&", "©"]
    ]
    return header_y, items


def _verify_smart_connect(debug=False):
    print("\n[VERIFY] Starting Smart Connect UI state verification...")
    img, region = _capture_content_area(debug=debug)
    all_words = _ocr_all_text(img, region)
    full_text = " ".join(w["text"] for w in all_words).lower()

    results = {}

    # Check 0: Feature availability
    if "available" in full_text and "pc" in full_text and "isn" in full_text:
        results["Feature Available"] = (
            "FAIL", SC_STATE_UNAVAILABLE,
            "Smart Connect isn't available on this PC",
        )
        return results

    results["Feature Available"] = ("PASS", SC_STATE_PASS, "Smart Connect is available")

    # Check 1: My Networks
    if "known" in full_text and "networks" in full_text and "area" in full_text:
        results["My Networks"] = (
            "FAIL", SC_STATE_NO_MY_NET,
            "No known networks available in your area",
        )
    else:
        _, my_items = _get_section_items(all_words, "My", ["Other"])
        meaningful = [w for w in my_items if len(w) > 2 and w.lower() not in
                      ["networks", "other", "no", "known", "area"]]
        if meaningful:
            results["My Networks"] = (
                "PASS", SC_STATE_PASS,
                f"Found {len(meaningful)} network(s): {', '.join(meaningful[:5])}",
            )
        else:
            results["My Networks"] = (
                "FAIL", SC_STATE_NO_MY_NET,
                "No network entries detected under My Networks",
            )

    # Check 2: Other Networks
    if "nearby" in full_text and "detected" in full_text:
        results["Other Networks"] = (
            "FAIL", SC_STATE_NO_OTHER_NET,
            "No networks detected nearby",
        )
    else:
        _, other_items = _get_section_items(all_words, "Other", ["Never", "Connect"])
        meaningful2 = [w for w in other_items if len(w) > 2 and w.lower() not in
                       ["networks", "other", "no", "nearby", "detected"]]
        if meaningful2:
            results["Other Networks"] = (
                "PASS", SC_STATE_PASS,
                f"Found {len(meaningful2)} network(s): {', '.join(meaningful2[:5])}",
            )
        else:
            results["Other Networks"] = (
                "FAIL", SC_STATE_NO_OTHER_NET,
                "No network entries detected under Other Networks",
            )

    return results


# ── Tool entry point ──────────────────────────────────────────────────────────
def sc_status_check(attach_timeout: int = 30, debug: bool = False) -> dict:
    """Launch Intel Connection Suite, navigate to Smart Connect, and verify its UI
    state via OCR. Mirrors the standalone ICS_SC_Check.py __main__ flow and returns
    a structured result of the Smart Connect verification checks."""
    try:
        _configure_dpi_and_ocr() 
        print("[STEP 1] Launching ICS...")
        _launch_ics()
        _attach_ics(timeout=attach_timeout) 
        print("[STEP 2] Waiting 5s for ICS to fully load...")
        time.sleep(5) 
        print("[STEP 3] Navigating to Smart Connect...")
        nav_ok = _navigate_to("Smart Connect")
        if not nav_ok:
            return {
                "status": "error",
                "error": "Could not navigate to Smart Connect.",
            } 
        print("[STEP 4] Waiting 10s for Smart Connect to load...")
        for i in range(10, 0, -1):
            print(f"  {i}s remaining...", end="\r")
            time.sleep(1)
        print() 
        print("[STEP 5] Verifying Smart Connect UI state...")
        results = _verify_smart_connect(debug=debug)

        checks = {
            check: {"result": status, "state": state, "detail": detail}
            for check, (status, state, detail) in results.items()
        }
        overall_pass = all(v["result"] == "PASS" for v in checks.values())

        return {
            "status": "success",
            "overall": "PASS" if overall_pass else "FAIL",
            "checks": checks,
        }
    except ImportError as e:
        return {
            "error": f"Missing dependency: {e}. Run: pip install pywin32 pytesseract Pillow pywinauto"
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# Map of function name -> callable
ICS_TOOL_FUNCTIONS = {
    "sc_status_check": sc_status_check,
}

# Anthropic-compatible tool definitions
ICS_ANTHROPIC_TOOLS = [
    {
        "name": "sc_status_check",
        "description": (
            "Launch the Intel Connection Suite (ICS/ICPS), navigate to the Smart "
            "Connect page, and verify its UI state using OCR. Returns whether Smart "
            "Connect is available and lists the detected 'My Networks' and 'Other "
            "Networks' entries. Requires the ICS app, Tesseract-OCR, pywin32, "
            "pytesseract, Pillow, and pywinauto to be installed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "attach_timeout": {
                    "type": "integer",
                    "description": "Seconds to wait for the ICS window to appear. Defaults to 30.",
                },
                "debug": {
                    "type": "boolean",
                    "description": "Save a debug screenshot of the content area. Defaults to false.",
                },
            },
            "required": [],
        },
    },
]

# OpenAI-compatible tool definitions
ICS_OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "sc_status_check",
            "description": (
                "Launch the Intel Connection Suite (ICS/ICPS), navigate to the Smart "
                "Connect page, and verify its UI state using OCR. Returns whether Smart "
                "Connect is available and lists the detected 'My Networks' and 'Other "
                "Networks' entries. Requires the ICS app, Tesseract-OCR, pywin32, "
                "pytesseract, Pillow, and pywinauto to be installed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "attach_timeout": {
                        "type": "integer",
                        "description": "Seconds to wait for the ICS window to appear. Defaults to 30.",
                    },
                    "debug": {
                        "type": "boolean",
                        "description": "Save a debug screenshot of the content area. Defaults to false.",
                    },
                },
                "required": [],
            },
        },
    },
]


if __name__ == "__main__":
    sc_status_check()
