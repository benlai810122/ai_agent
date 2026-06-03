import subprocess


def detect_bluetooth_mouse() -> dict:
    """Detect if any Bluetooth mouse is currently connected to the laptop."""
    try:
        # Query Bluetooth HID (mouse) devices via Windows PnP
        cmd = [
            'powershell', '-Command',
            'Get-PnpDevice -Class Mouse | Select-Object FriendlyName, InstanceId, Status | Format-List'
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

        if proc.returncode != 0:
            return {"error": f"PowerShell command failed: {proc.stderr.strip()}"}

        if not proc.stdout.strip():
            return {
                "status": "not_found",
                "message": "No mouse devices found on this system.",
                "bluetooth_mice": [],
            }

        # Parse device blocks
        devices_raw = proc.stdout.strip().split("\n\n")
        all_mice = []
        bluetooth_mice = []

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

            # Identify Bluetooth mice by InstanceId patterns:
            # - BTHLE / BTHENUM / BTH prefix for classic Bluetooth HID
            # - {00001812-...} is the Bluetooth HID over GATT UUID
            upper_id = instance_id.upper()
            is_bluetooth = any(
                tag in upper_id
                for tag in ["BTHLE", "BTHENUM", "BLUETOOTHMOUSE", "BTH\\"]
            ) or "00001812-0000-1000-8000-00805F9B34FB" in upper_id

            mouse_info = {
                "name": friendly_name,
                "instance_id": instance_id,
                "status": status,
                "is_bluetooth": is_bluetooth,
                "connected": status.lower() == "ok",
            }

            all_mice.append(mouse_info)
            if is_bluetooth:
                bluetooth_mice.append(mouse_info)

        connected_bt_mice = [m for m in bluetooth_mice if m["connected"]]

        return {
            "status": "success",
            "bluetooth_mouse_found": len(bluetooth_mice) > 0,
            "bluetooth_mouse_connected": len(connected_bt_mice) > 0,
            "connected_count": len(connected_bt_mice),
            "total_bluetooth_mice": len(bluetooth_mice),
            "bluetooth_mice": bluetooth_mice,
            "all_mice": all_mice,
        }
    except Exception as e:
        return {"error": str(e)}


MOUSE_KEYBOARD_ANTHROPIC_TOOLS = [
    {
        "name": "detect_bluetooth_mouse",
        "description": "Detect if any Bluetooth mouse is currently connected to the laptop. Returns a list of all mice found, highlighting which are Bluetooth and whether they are connected.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

MOUSE_KEYBOARD_TOOL_FUNCTIONS = {
    "detect_bluetooth_mouse": detect_bluetooth_mouse,
}
