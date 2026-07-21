import asyncio
import json
import os
import time


def scan_bluetooth_devices(duration: int = 5) -> dict:
    """Scan for nearby Bluetooth devices and return the top 10 devices by signal strength (RSSI)."""
    try:
        from bleak import BleakScanner

        async def _scan():
            devices_and_advs = await BleakScanner.discover(timeout=duration, return_adv=True)
            return [
                {
                    "name": d.name or "Unknown",
                    "address": d.address,
                    "rssi": adv.rssi,
                }
                for d, adv in devices_and_advs.values()
            ]

        discovered = asyncio.run(_scan())
        total_found = len(discovered)
        # Keep only the 10 strongest devices (highest RSSI = strongest signal).
        top_devices = sorted(
            discovered, key=lambda dev: dev["rssi"], reverse=True
        )[:10]
        return {
            "status": "success",
            "device_count": len(top_devices),
            "total_found": total_found,
            "devices": top_devices,
        }
    except ImportError:
        return {"error": "bleak package not installed. Run: pip install bleak"}
    except Exception as e:
        return {"error": str(e)}


def check_bluetooth_connection_status(device_name: str = "", address: str = "") -> dict:
    """Check if a specific Bluetooth device is currently connected. Supports both classic Bluetooth and BLE devices."""
    try:
        import subprocess
        import re

        if not device_name and not address:
            return {"error": "Please provide either device_name or address to search for."}

        search_name = device_name.lower() if device_name else ""
        search_addr = address.upper() if address else ""

        results = []

        # Check paired/connected classic Bluetooth devices via Windows PowerShell
        try:
            # Use audio endpoint status for accurate connection detection
            audio_cmd = [
                'powershell', '-Command',
                'Get-PnpDevice -Class AudioEndpoint | Select-Object FriendlyName, Status | Format-List'
            ]
            audio_proc = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=10)

            # Also get paired Bluetooth devices
            bt_cmd = [
                'powershell', '-Command',
                'Get-PnpDevice -Class Bluetooth | Select-Object FriendlyName, InstanceId, Status | Format-List'
            ]
            bt_proc = subprocess.run(bt_cmd, capture_output=True, text=True, timeout=10)

            # Build set of connected audio device names
            connected_audio = set()
            if audio_proc.stdout.strip():
                current_name = ""
                for line in audio_proc.stdout.split("\n"):
                    if "FriendlyName" in line:
                        _, _, val = line.partition(":")
                        current_name = val.strip()
                    elif "Status" in line and current_name:
                        _, _, val = line.partition(":")
                        if val.strip().lower() == "ok":
                            connected_audio.add(current_name.lower())
                        current_name = ""

            # Parse Bluetooth devices
            if bt_proc.stdout.strip():
                devices_raw = bt_proc.stdout.strip().split("\n\n")
                for block in devices_raw:
                    lines = block.strip().split("\n")
                    dev_info = {}
                    for line in lines:
                        if ":" in line:
                            key, _, val = line.partition(":")
                            dev_info[key.strip()] = val.strip()

                    friendly_name = dev_info.get("FriendlyName", "")
                    instance_id = dev_info.get("InstanceId", "")
                    status = dev_info.get("Status", "")

                    if not friendly_name:
                        continue

                    name_match = search_name and search_name in friendly_name.lower()
                    addr_match = search_addr and search_addr in instance_id.upper().replace("_", ":")
                    if name_match or addr_match:
                        # Check if any audio endpoint with this name is actively connected
                        is_audio_connected = any(
                            friendly_name.lower() in audio_name for audio_name in connected_audio
                        ) or any(
                            search_name in audio_name for audio_name in connected_audio
                        ) if search_name else False
                        results.append({
                            "name": friendly_name,
                            "instance_id": instance_id,
                            "paired": status.lower() == "ok",
                            "connected": is_audio_connected,
                            "type": "classic/paired",
                        })
        except Exception as e:
            results.append({"error_classic_scan": str(e)})

        # Also check BLE devices via bleak
        try:
            from bleak import BleakScanner

            async def _scan():
                devices_and_advs = await BleakScanner.discover(timeout=5, return_adv=True)
                found = []
                for d, adv in devices_and_advs.values():
                    name = d.name or "Unknown"
                    addr = d.address.upper()
                    name_match = search_name and search_name in name.lower()
                    addr_match = search_addr and search_addr == addr
                    if name_match or addr_match:
                        found.append({
                            "name": name,
                            "address": d.address,
                            "rssi": adv.rssi,
                            "type": "BLE",
                        })
                return found

            ble_found = asyncio.run(_scan())
            results.extend(ble_found)
        except Exception as e:
            results.append({"error_ble_scan": str(e)})

        if not results:
            return {
                "status": "not_found",
                "message": "Device not found. It may be off, out of range, or not paired.",
                "search_name": device_name,
                "search_address": address,
            }

        return {"status": "success", "devices": results}
    except Exception as e:
        return {"error": str(e)}


def reconnect_bluetooth_via_ui(device_name: str) -> dict:
    """Reconnect a paired Bluetooth device by automating the Windows Bluetooth Settings UI.
    Opens Bluetooth settings, uses Windows UI Automation to find and click the Connect button, and verifies connection."""
    try:
        import subprocess
        import pyautogui

        steps_log = []

        # Step 1: Open Bluetooth & Devices settings
        subprocess.Popen(['start', 'ms-settings:bluetooth'], shell=True)
        time.sleep(3)
        steps_log.append("Opened Bluetooth & Devices settings")

        # Step 2: Bring settings window to foreground
        subprocess.run([
            'powershell', '-Command',
            '(New-Object -ComObject WScript.Shell).AppActivate("Settings")'
        ], capture_output=True, text=True, timeout=5)
        time.sleep(1)

        # Step 3: Use UI Automation to find the device and click Connect
        ps_script = f'''
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement

# Find the Settings window
$settingsCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, "Settings"
)
$settingsWin = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $settingsCondition)

if (-not $settingsWin) {{
    $settingsCondition2 = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ClassNameProperty, "ApplicationFrameWindow"
    )
    $appWindows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $settingsCondition2)
    foreach ($w in $appWindows) {{
        if ($w.Current.Name -like "*Settings*" -or $w.Current.Name -like "*Bluetooth*") {{
            $settingsWin = $w
            break
        }}
    }}
}}

if (-not $settingsWin) {{
    Write-Output "ERROR: Settings window not found"
    exit 1
}}

# Search for all buttons in the Settings window
$buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button
)
$allButtons = $settingsWin.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)

$connectButton = $null
$textCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Text
)

# Look for a Connect button associated with the device name
foreach ($btn in $allButtons) {{
    if ($btn.Current.Name -ne "Connect") {{ continue }}

    # Check parent and grandparent for device name text
    $parent = [System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($btn)
    if (-not $parent) {{ continue }}

    foreach ($ancestor in @($parent, [System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($parent))) {{
        if (-not $ancestor) {{ continue }}
        $textElements = $ancestor.FindAll([System.Windows.Automation.TreeScope]::Descendants, $textCondition)
        foreach ($t in $textElements) {{
            if ($t.Current.Name -like "*{device_name}*") {{
                $connectButton = $btn
                break
            }}
        }}
        if ($connectButton) {{ break }}
    }}
    if ($connectButton) {{ break }}
}}

# Fallback: click the first Connect button found
if (-not $connectButton) {{
    foreach ($btn in $allButtons) {{
        if ($btn.Current.Name -eq "Connect") {{
            $connectButton = $btn
            Write-Output "Using fallback Connect button"
            break
        }}
    }}
}}

if ($connectButton) {{
    $invokePattern = $connectButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $invokePattern.Invoke()
    Write-Output "SUCCESS: Clicked Connect button"
}} else {{
    Write-Output "ERROR: No Connect button found"
}}
'''
        result = subprocess.run(
            ['powershell', '-Command', ps_script],
            capture_output=True, text=True, timeout=30
        )

        ps_output = result.stdout.strip()
        steps_log.append(f"UI Automation: {ps_output}")

        if "SUCCESS" not in ps_output:
            pyautogui.hotkey('alt', 'F4')
            return {
                "status": "connect_button_not_found",
                "message": f"Could not find Connect button for '{device_name}'.",
                "steps": steps_log,
            }

        # Step 4: Wait for connection to establish
        time.sleep(8)
        steps_log.append("Waited 8 seconds for connection")

        # Step 5: Close settings
        pyautogui.hotkey('alt', 'F4')
        time.sleep(1)

        # Step 6: Verify connection
        verify_result = check_bluetooth_connection_status(device_name=device_name)
        is_connected = False
        if verify_result.get("status") == "success":
            for dev in verify_result.get("devices", []):
                if dev.get("connected", False):
                    is_connected = True
                    break

        steps_log.append(f"Verification: connected={is_connected}")

        if is_connected:
            return {
                "status": "success",
                "message": f"Successfully reconnected '{device_name}'.",
                "connected": True,
                "steps": steps_log,
            }
        else:
            return {
                "status": "connection_uncertain",
                "message": f"Clicked Connect for '{device_name}' but verification shows not connected. The device may need more time or may be out of range.",
                "connected": False,
                "steps": steps_log,
                "verification": verify_result,
            }
    except Exception as e:
        return {"error": str(e)}


def set_bluetooth_radio_via_ui(turn_on: bool) -> dict:
    """Turn laptop Bluetooth on or off by automating the Windows Bluetooth Settings UI toggle."""
    try:
        import subprocess
        import pyautogui

        desired_state = "on" if turn_on else "off"
        steps_log = []

        # Step 1: Open Bluetooth & Devices settings
        subprocess.Popen(['start', 'ms-settings:bluetooth'], shell=True)
        time.sleep(3)
        steps_log.append("Opened Bluetooth & Devices settings")

        # Step 2: Bring settings window to foreground
        subprocess.run([
            'powershell', '-Command',
            '(New-Object -ComObject WScript.Shell).AppActivate("Settings")'
        ], capture_output=True, text=True, timeout=5)
        time.sleep(1)

        # Step 3: Find and set Bluetooth toggle state via UI Automation
        ps_script = f'''
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$targetState = "{desired_state}"
$root = [System.Windows.Automation.AutomationElement]::RootElement

$settingsCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, "Settings"
)
$settingsWin = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $settingsCondition)

if (-not $settingsWin) {{
    $settingsCondition2 = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ClassNameProperty, "ApplicationFrameWindow"
    )
    $appWindows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $settingsCondition2)
    foreach ($w in $appWindows) {{
        if ($w.Current.Name -like "*Settings*" -or $w.Current.Name -like "*Bluetooth*") {{
            $settingsWin = $w
            break
        }}
    }}
}}

if (-not $settingsWin) {{
    Write-Output "ERROR: Settings window not found"
    exit 1
}}

$trueCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::IsEnabledProperty, $true
)
$allElements = $settingsWin.FindAll([System.Windows.Automation.TreeScope]::Descendants, $trueCondition)

$toggleElement = $null
$togglePattern = $null

for ($i = 0; $i -lt $allElements.Count; $i++) {{
    $el = $allElements.Item($i)
    $name = $el.Current.Name
    if ([string]::IsNullOrWhiteSpace($name)) {{ continue }}
    if ($name -notlike "*Bluetooth*") {{ continue }}

    try {{
        $candidatePattern = $el.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
        if ($candidatePattern) {{
            $toggleElement = $el
            $togglePattern = $candidatePattern
            break
        }}
    }} catch {{
        continue
    }}
}}

if (-not $toggleElement -or -not $togglePattern) {{
    Write-Output "ERROR: Bluetooth toggle not found"
    exit 1
}}

$currentState = $togglePattern.Current.ToggleState
$currentStateText = if ($currentState -eq [System.Windows.Automation.ToggleState]::On) {{ "on" }} else {{ "off" }}

if ($targetState -eq $currentStateText) {{
    Write-Output "NOOP: Bluetooth already $currentStateText"
    exit 0
}}

try {{
    $invokePattern = $toggleElement.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $invokePattern.Invoke()
}} catch {{
    $togglePattern.Toggle()
}}

Start-Sleep -Milliseconds 900

$updatedPattern = $toggleElement.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
$updatedState = $updatedPattern.Current.ToggleState
$updatedStateText = if ($updatedState -eq [System.Windows.Automation.ToggleState]::On) {{ "on" }} else {{ "off" }}

if ($updatedStateText -eq $targetState) {{
    Write-Output "SUCCESS: Bluetooth turned $updatedStateText"
}} else {{
    Write-Output "ERROR: Toggle attempted but state is still $updatedStateText"
}}
'''

        result = subprocess.run(
            ['powershell', '-Command', ps_script],
            capture_output=True, text=True, timeout=35
        )

        ps_output = result.stdout.strip()
        if result.stderr and result.stderr.strip():
            ps_output = f"{ps_output} | STDERR: {result.stderr.strip()}" if ps_output else result.stderr.strip()
        steps_log.append(f"UI Automation: {ps_output}")

        # Step 4: Close settings
        pyautogui.hotkey('alt', 'F4')
        time.sleep(1)

        if "SUCCESS" in ps_output:
            return {
                "status": "success",
                "message": f"Successfully turned Bluetooth {desired_state}.",
                "bluetooth_state": desired_state,
                "steps": steps_log,
            }

        if "NOOP" in ps_output:
            return {
                "status": "already_in_desired_state",
                "message": f"Bluetooth is already {desired_state}.",
                "bluetooth_state": desired_state,
                "steps": steps_log,
            }

        return {
            "status": "toggle_failed",
            "message": f"Could not turn Bluetooth {desired_state} via UI automation.",
            "steps": steps_log,
        }
    except Exception as e:
        return {"error": str(e)}


def disconnect_bluetooth_via_ui(device_name: str) -> dict:
    """Disconnect a connected Bluetooth device by automating the Windows Bluetooth Settings UI.
    Opens Bluetooth settings, uses Windows UI Automation to find and click the Disconnect button, and verifies disconnection."""
    try:
        import subprocess
        import pyautogui

        steps_log = []

        # Step 1: Open Bluetooth & Devices settings
        subprocess.Popen(['start', 'ms-settings:bluetooth'], shell=True)
        time.sleep(3)
        steps_log.append("Opened Bluetooth & Devices settings")

        # Step 2: Bring settings window to foreground
        subprocess.run([
            'powershell', '-Command',
            '(New-Object -ComObject WScript.Shell).AppActivate("Settings")'
        ], capture_output=True, text=True, timeout=5)
        time.sleep(1)

        # Step 3: Use UI Automation to find the device and click Disconnect
        ps_script = f'''
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement

$settingsCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, "Settings"
)
$settingsWin = $root.FindFirst([System.Windows.Automation.TreeScope]::Children, $settingsCondition)

if (-not $settingsWin) {{
    $settingsCondition2 = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ClassNameProperty, "ApplicationFrameWindow"
    )
    $appWindows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $settingsCondition2)
    foreach ($w in $appWindows) {{
        if ($w.Current.Name -like "*Settings*" -or $w.Current.Name -like "*Bluetooth*") {{
            $settingsWin = $w
            break
        }}
    }}
}}

if (-not $settingsWin) {{
    Write-Output "ERROR: Settings window not found"
    exit 1
}}

$buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button
)
$allButtons = $settingsWin.FindAll([System.Windows.Automation.TreeScope]::Descendants, $buttonCondition)

$disconnectButton = $null
$textCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Text
)

foreach ($btn in $allButtons) {{
    if ($btn.Current.Name -ne "Disconnect") {{ continue }}

    $parent = [System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($btn)
    if (-not $parent) {{ continue }}

    foreach ($ancestor in @($parent, [System.Windows.Automation.TreeWalker]::RawViewWalker.GetParent($parent))) {{
        if (-not $ancestor) {{ continue }}
        $textElements = $ancestor.FindAll([System.Windows.Automation.TreeScope]::Descendants, $textCondition)
        foreach ($t in $textElements) {{
            if ($t.Current.Name -like "*{device_name}*") {{
                $disconnectButton = $btn
                break
            }}
        }}
        if ($disconnectButton) {{ break }}
    }}
    if ($disconnectButton) {{ break }}
}}

if (-not $disconnectButton) {{
    foreach ($btn in $allButtons) {{
        if ($btn.Current.Name -eq "Disconnect") {{
            $disconnectButton = $btn
            Write-Output "Using fallback Disconnect button"
            break
        }}
    }}
}}

if ($disconnectButton) {{
    $invokePattern = $disconnectButton.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $invokePattern.Invoke()
    Write-Output "SUCCESS: Clicked Disconnect button"
}} else {{
    Write-Output "ERROR: No Disconnect button found"
}}
'''
        result = subprocess.run(
            ['powershell', '-Command', ps_script],
            capture_output=True, text=True, timeout=30
        )

        ps_output = result.stdout.strip()
        steps_log.append(f"UI Automation: {ps_output}")

        if "SUCCESS" not in ps_output:
            pyautogui.hotkey('alt', 'F4')
            return {
                "status": "disconnect_button_not_found",
                "message": f"Could not find Disconnect button for '{device_name}'. Device may not be connected.",
                "steps": steps_log,
            }

        # Step 4: Wait for disconnection
        time.sleep(3)
        steps_log.append("Waited 3 seconds for disconnection")

        # Step 5: Close settings
        pyautogui.hotkey('alt', 'F4')
        time.sleep(1)

        # Step 6: Verify disconnection
        verify_result = check_bluetooth_connection_status(device_name=device_name)
        is_connected = False
        if verify_result.get("status") == "success":
            for dev in verify_result.get("devices", []):
                if dev.get("connected", False):
                    is_connected = True
                    break

        steps_log.append(f"Verification: connected={is_connected}")

        if not is_connected:
            return {
                "status": "success",
                "message": f"Successfully disconnected '{device_name}'.",
                "connected": False,
                "steps": steps_log,
            }
        else:
            return {
                "status": "disconnect_uncertain",
                "message": f"Clicked Disconnect for '{device_name}' but verification shows still connected.",
                "connected": True,
                "steps": steps_log,
                "verification": verify_result,
            }
    except Exception as e:
        return {"error": str(e)}


# Map of function name -> callable
BLUETOOTH_TOOL_FUNCTIONS = {
    "scan_bluetooth_devices": scan_bluetooth_devices,
    "check_bluetooth_connection_status": check_bluetooth_connection_status,
    "reconnect_bluetooth_via_ui": reconnect_bluetooth_via_ui,
    "set_bluetooth_radio_via_ui": set_bluetooth_radio_via_ui,
    "disconnect_bluetooth_via_ui": disconnect_bluetooth_via_ui,
}

# Anthropic-compatible tool definitions
BLUETOOTH_ANTHROPIC_TOOLS = [
    {
        "name": "scan_bluetooth_devices",
        "description": "Scan for nearby Bluetooth (BLE) devices and return a list of discovered devices with name, address, and signal strength.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration": {"type": "integer", "description": "Scan duration in seconds. Defaults to 5."}
            },
            "required": [],
        },
    },
    {
        "name": "check_bluetooth_connection_status",
        "description": "Check if a specific Bluetooth device is currently connected or nearby. Search by device name (partial match) or MAC address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string", "description": "The name (or partial name) of the Bluetooth device to search for, e.g. 'AirPods' or 'Sony WH'."},
                "address": {"type": "string", "description": "The MAC address of the device to check."}
            },
            "required": [],
        },
    },
    {
        "name": "reconnect_bluetooth_via_ui",
        "description": "Reconnect a paired Bluetooth device by automating the Windows Bluetooth Settings UI. Opens settings, uses UI Automation to find and click the Connect button, then verifies connection.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string", "description": "The name (or partial name) of the Bluetooth device to reconnect, e.g. 'Dell WL5024' or 'PLT Focus'."}
            },
            "required": ["device_name"],
        },
    },
    {
        "name": "set_bluetooth_radio_via_ui",
        "description": "Turn laptop Bluetooth on or off by automating the Windows Bluetooth Settings UI toggle switch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "turn_on": {"type": "boolean", "description": "Set true to turn Bluetooth on, or false to turn Bluetooth off."}
            },
            "required": ["turn_on"],
        },
    },
    {
        "name": "disconnect_bluetooth_via_ui",
        "description": "Disconnect a connected Bluetooth device by automating the Windows Bluetooth Settings UI. Opens settings, uses UI Automation to find and click the Disconnect button, then verifies disconnection.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string", "description": "The name (or partial name) of the Bluetooth device to disconnect, e.g. 'Dell WL5024' or 'PLT Focus'."}
            },
            "required": ["device_name"],
        },
    },
]

# OpenAI-compatible tool definitions
BLUETOOTH_OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "scan_bluetooth_devices",
            "description": "Scan for nearby Bluetooth (BLE) devices and return a list of discovered devices with name, address, and signal strength.",
            "parameters": {
                "type": "object",
                "properties": {
                    "duration": {"type": "integer", "description": "Scan duration in seconds. Defaults to 5."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_bluetooth_connection_status",
            "description": "Check if a specific Bluetooth device is currently connected or nearby. Search by device name (partial match) or MAC address.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "The name (or partial name) of the Bluetooth device to search for, e.g. 'AirPods' or 'Sony WH'."},
                    "address": {"type": "string", "description": "The MAC address of the device to check."}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reconnect_bluetooth_via_ui",
            "description": "Reconnect a paired Bluetooth device by automating the Windows Bluetooth Settings UI. Opens settings, uses UI Automation to find and click the Connect button, then verifies connection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "The name (or partial name) of the Bluetooth device to reconnect, e.g. 'Dell WL5024' or 'PLT Focus'."}
                },
                "required": ["device_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_bluetooth_radio_via_ui",
            "description": "Turn laptop Bluetooth on or off by automating the Windows Bluetooth Settings UI toggle switch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "turn_on": {"type": "boolean", "description": "Set true to turn Bluetooth on, or false to turn Bluetooth off."}
                },
                "required": ["turn_on"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "disconnect_bluetooth_via_ui",
            "description": "Disconnect a connected Bluetooth device by automating the Windows Bluetooth Settings UI. Opens settings, uses UI Automation to find and click the Disconnect button, then verifies disconnection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "device_name": {"type": "string", "description": "The name (or partial name) of the Bluetooth device to disconnect, e.g. 'Dell WL5024' or 'PLT Focus'."}
                },
                "required": ["device_name"],
            },
        },
    },
]
