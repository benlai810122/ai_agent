import serial
import serial.tools.list_ports
import re
import time


_serial_connection: serial.Serial = None


def _dexarm_send_cmd(data: str, wait: bool = True, timeout: float = 10.0) -> str:
    """Send a G-code command to the Dexarm and optionally wait for 'ok'."""
    global _serial_connection
    _serial_connection.write(data.encode())
    if not wait:
        _serial_connection.reset_input_buffer()
        return ""
    output_lines = []
    start = time.time()
    while True:
        if time.time() - start > timeout:
            return "\n".join(output_lines) + "\n[timeout waiting for ok]"
        serial_str = _serial_connection.readline().decode("utf-8", errors="replace")
        if len(serial_str) > 0:
            output_lines.append(serial_str.strip())
            if serial_str.find("ok") > -1:
                break
    return "\n".join(output_lines)


def dexarm_board_check(target_port_desc: str = "USB-SERIAL CH340") -> dict:
    """Check if a Dexarm is connected by searching for a matching COM port description."""
    try:
        ports = serial.tools.list_ports.comports()
        for port, desc, hwid in ports:
            if target_port_desc in desc:
                return {"status": "success", "found": True, "port": port, "description": desc}
        return {"status": "success", "found": False, "message": f"No Dexarm matching '{target_port_desc}' found."}
    except Exception as e:
        return {"error": str(e)}


def dexarm_connect(port: str = None, baud_rate: int = 115200, timeout: int = 5) -> dict:
    """Connect to a Dexarm via serial port. Auto-detects the port using hardware ID VID_0483&PID_5740 if no port is specified."""
    global _serial_connection
    try:
        target_hwid = "VID:PID=0483:5740"
        if port is None:
            ports = serial.tools.list_ports.comports()
            for p in ports:
                if target_hwid in p.hwid:
                    port = p.device
                    break
            if port is None:
                return {"status": "failure", "message": f"No Dexarm found with hardware ID '{target_hwid}'."}
        else:
            # Verify the specified port matches the expected hardware ID
            ports = serial.tools.list_ports.comports()
            matched = False
            for p in ports:
                if p.device == port and target_hwid in p.hwid:
                    matched = True
                    break
            if not matched:
                return {"status": "failure", "message": f"Port {port} does not match Dexarm hardware ID '{target_hwid}'."}

        if _serial_connection and _serial_connection.is_open:
            _serial_connection.close()
        _serial_connection = serial.Serial(port, baud_rate, timeout=timeout)
        if _serial_connection.is_open:
            return {"status": "success", "port": port, "baud_rate": baud_rate, "message": f"Connected to Dexarm on {port} at {baud_rate} baud."}
        else:
            return {"status": "failure", "message": f"Failed to open serial port {port}."}
    except Exception as e:
        return {"error": str(e)}


def dexarm_disconnect() -> dict:
    """Disconnect from the Dexarm and release the serial port."""
    global _serial_connection
    try:
        if _serial_connection and _serial_connection.is_open:
            _serial_connection.close()
            _serial_connection = None
            return {"status": "success", "message": "Dexarm disconnected."}
        return {"status": "success", "message": "No active connection to close."}
    except Exception as e:
        return {"error": str(e)}


def dexarm_go_home() -> dict:
    """Go to home position and enable motors. Should be called each time after power on."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M1112\r")
        return {"status": "success", "action": "go_home", "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_move_to(x: float = None, y: float = None, z: float = None, e: float = None,
                   feedrate: int = 2000, mode: str = "G1", wait: bool = True) -> dict:
    """
    Move the Dexarm to a cartesian position.
    mode G1 = linear move (default), G0 = fast move.
    The center of the Y axis is 300mm.
    """
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        cmd = mode + "F" + str(feedrate)
        if x is not None:
            cmd += "X" + str(round(x))
        if y is not None:
            cmd += "Y" + str(round(y))
        if z is not None:
            cmd += "Z" + str(round(z))
        if e is not None:
            cmd += "E" + str(round(e))
        cmd += "\r\n"
        resp = _dexarm_send_cmd(cmd, wait=wait)
        # A move is acked ('ok') as soon as it is QUEUED, not when the arm
        # physically arrives. M400 blocks until the motion buffer drains, so the
        # function only returns once the move has actually finished. Without this,
        # back-to-back callers (e.g. the deterministic script runner) race ahead
        # and take screenshots / send clicks before the arm has moved.
        if wait:
            _dexarm_send_cmd("M400\r", wait=True)
        return {"status": "success", "action": "move_to", "command": cmd.strip(), "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_get_current_position() -> dict:
    """Get the current position of the Dexarm (X, Y, Z, E and theta A, B, C)."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        _serial_connection.reset_input_buffer()
        _serial_connection.write('M114\r'.encode())
        x, y, z, e, a, b, c = None, None, None, None, None, None, None
        start = time.time()
        while True:
            if time.time() - start > 10:
                break
            serial_str = _serial_connection.readline().decode("utf-8", errors="replace")
            if serial_str.find("X:") > -1:
                temp = re.findall(r"[-+]?\d*\.\d+|\d+", serial_str)
                x, y, z, e = float(temp[0]), float(temp[1]), float(temp[2]), float(temp[3])
            if serial_str.find("DEXARM Theta") > -1:
                temp = re.findall(r"[-+]?\d*\.\d+|\d+", serial_str)
                a, b, c = float(temp[0]), float(temp[1]), float(temp[2])
            if serial_str.find("ok") > -1:
                break
        return {"status": "success", "position": {"x": x, "y": y, "z": z, "e": e, "theta_a": a, "theta_b": b, "theta_c": c}}
    except Exception as e:
        return {"error": str(e)}


def dexarm_set_work_origin() -> dict:
    """Set the current position as the new work origin."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("G92 X0 Y0 Z0 E0\r")
        return {"status": "success", "action": "set_work_origin", "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_set_module_type(module_type: int) -> dict:
    """
    Set the type of end effector module.
    0 = Pen holder, 1 = Laser engraving, 2 = Pneumatic, 3 = 3D printing.
    """
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M888 P" + str(module_type) + "\r")
        return {"status": "success", "action": "set_module_type", "module_type": module_type, "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_get_module_type() -> dict:
    """Get the current end effector module type."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        _serial_connection.reset_input_buffer()
        _serial_connection.write('M888\r'.encode())
        module_type = "UNKNOWN"
        start = time.time()
        while True:
            if time.time() - start > 10:
                break
            serial_str = _serial_connection.readline().decode("utf-8", errors="replace")
            if "PEN" in serial_str:
                module_type = "PEN"
            elif "LASER" in serial_str:
                module_type = "LASER"
            elif "PUMP" in serial_str:
                module_type = "PUMP"
            elif "3D" in serial_str:
                module_type = "3D"
            if serial_str.find("ok") > -1:
                break
        return {"status": "success", "module_type": module_type}
    except Exception as e:
        return {"error": str(e)}


def dexarm_set_acceleration(acceleration: int, travel_acceleration: int, retract_acceleration: int = 60) -> dict:
    """Set preferred starting acceleration for moves (printing, travel, retract)."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        cmd = "M204P" + str(acceleration) + "T" + str(travel_acceleration) + "T" + str(retract_acceleration) + "\r\n"
        resp = _dexarm_send_cmd(cmd)
        return {"status": "success", "action": "set_acceleration", "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_delay_ms(value: int) -> dict:
    """Pause the Dexarm command queue for a specified number of milliseconds."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("G4 P" + str(value) + "\r")
        return {"status": "success", "action": "delay_ms", "value_ms": value, "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_delay_s(value: int) -> dict:
    """Pause the Dexarm command queue for a specified number of seconds."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("G4 S" + str(value) + "\r")
        return {"status": "success", "action": "delay_s", "value_s": value, "response": resp}
    except Exception as e:
        return {"error": str(e)}


# ── Soft Gripper ──

def dexarm_soft_gripper_pick() -> dict:
    """Close the soft gripper to pick up an object."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M1001\r")
        return {"status": "success", "action": "soft_gripper_pick", "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_soft_gripper_place() -> dict:
    """Wide-open the soft gripper to place an object."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M1000\r")
        return {"status": "success", "action": "soft_gripper_place", "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_soft_gripper_nature() -> dict:
    """Release the soft gripper to its natural state."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M1002\r")
        return {"status": "success", "action": "soft_gripper_nature", "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_soft_gripper_stop() -> dict:
    """Stop the soft gripper."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M1003\r")
        return {"status": "success", "action": "soft_gripper_stop", "response": resp}
    except Exception as e:
        return {"error": str(e)}


# ── Air Picker ──

def dexarm_air_picker_pick() -> dict:
    """Use the air picker to pick up an object."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M1000\r")
        return {"status": "success", "action": "air_picker_pick", "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_air_picker_place() -> dict:
    """Use the air picker to release an object."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M1001\r")
        return {"status": "success", "action": "air_picker_place", "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_air_picker_nature() -> dict:
    """Release air picker to natural state."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M1002\r")
        return {"status": "success", "action": "air_picker_nature", "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_air_picker_stop() -> dict:
    """Stop the air picker."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M1003\r")
        return {"status": "success", "action": "air_picker_stop", "response": resp}
    except Exception as e:
        return {"error": str(e)}


# ── Laser ──

def dexarm_laser_on(power: int = 0) -> dict:
    """Turn on the laser module with specified power (1-255)."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M3 S" + str(power) + "\r")
        return {"status": "success", "action": "laser_on", "power": power, "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_laser_off() -> dict:
    """Turn off the laser module."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M5\r")
        return {"status": "success", "action": "laser_off", "response": resp}
    except Exception as e:
        return {"error": str(e)}


# ── Conveyor Belt ──

def dexarm_conveyor_belt_forward(speed: int = 0) -> dict:
    """Move the conveyor belt forward at the given speed."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M2012 F" + str(speed) + "D0\r")
        return {"status": "success", "action": "conveyor_belt_forward", "speed": speed, "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_conveyor_belt_backward(speed: int = 0) -> dict:
    """Move the conveyor belt backward at the given speed."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M2012 F" + str(speed) + "D1\r")
        return {"status": "success", "action": "conveyor_belt_backward", "speed": speed, "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_conveyor_belt_stop() -> dict:
    """Stop the conveyor belt."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M2013\r")
        return {"status": "success", "action": "conveyor_belt_stop", "response": resp}
    except Exception as e:
        return {"error": str(e)}


# ── Sliding Rail ──

def dexarm_sliding_rail_init() -> dict:
    """Initialize the sliding rail."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        resp = _dexarm_send_cmd("M2005\r")
        return {"status": "success", "action": "sliding_rail_init", "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_clicking(x: float, y: float, z: float, z_diff: float = 5) -> dict:
    """Quickly click at a position by fast-moving to (x, y, z), then fast-releasing upward by z_diff."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        # Fast move above the target
        release_z = z + z_diff
        cmd_above = f"G0X{round(x)}Y{round(y)}Z{round(release_z)}\r\n"
        _dexarm_send_cmd(cmd_above, wait=True)
        # Fast press down
        cmd_press = f"G0X{round(x)}Y{round(y)}Z{round(z)}\r\n"
        _dexarm_send_cmd(cmd_press, wait=True)
        # Fast release up
        cmd_release = f"G0X{round(x)}Y{round(y)}Z{round(release_z)}\r\n"
        resp = _dexarm_send_cmd(cmd_release, wait=True)
        # The above/press/release moves are only QUEUED when 'ok' returns; wait for
        # the motion buffer to drain so the click has physically completed before
        # this function returns (otherwise the next step runs mid-click).
        _dexarm_send_cmd("M400\r", wait=True)
        return {"status": "success", "action": "clicking", "x": x, "y": y, "z_press": z, "z_release": release_z, "response": resp}
    except Exception as e:
        return {"error": str(e)}


def dexarm_send_raw_gcode(gcode: str, wait: bool = True) -> dict:
    """Send a raw G-code command to the Dexarm."""
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call dexarm_connect first."}
        if not gcode.endswith("\r"):
            gcode += "\r"
        resp = _dexarm_send_cmd(gcode, wait=wait)
        return {"status": "success", "action": "send_raw_gcode", "gcode": gcode.strip(), "response": resp}
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════
# Anthropic Tool Definitions
# ══════════════════════════════════════════════════════════════

DEXARM_ANTHROPIC_TOOLS = [
    {
        "name": "dexarm_board_check",
        "description": "Check if a Dexarm robotic arm is connected by searching for a matching COM port description. Returns the port name if found.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_port_desc": {
                    "type": "string",
                    "description": "A substring to match against COM port descriptions (e.g. 'CH340', 'USB-SERIAL'). Defaults to 'USB-SERIAL CH340'."
                }
            },
            "required": [],
        },
    },
    {
        "name": "dexarm_connect",
        "description": "Connect to a Dexarm robotic arm. Auto-detects the port using hardware ID VID_0483&PID_5740 if no port is specified. If a port is provided, it verifies the hardware ID before connecting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "string",
                    "description": "The COM port to connect to (e.g. 'COM3'). If omitted, auto-detects using hardware ID VID_0483&PID_5740."
                },
                "baud_rate": {
                    "type": "integer",
                    "description": "The baud rate for the serial connection. Defaults to 115200."
                },
                "timeout": {
                    "type": "integer",
                    "description": "Connection timeout in seconds. Defaults to 5."
                }
            },
            "required": [],
        },
    },
    {
        "name": "dexarm_disconnect",
        "description": "Disconnect from the Dexarm and release the serial port.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dexarm_go_home",
        "description": "Move the Dexarm to its home position and enable motors. Must be called after power on.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dexarm_move_to",
        "description": "Move the Dexarm to a cartesian position (X, Y, Z in mm). The center of Y axis is 300mm. Use mode G0 for fast move, G1 (default) for linear move.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "X position in mm."},
                "y": {"type": "number", "description": "Y position in mm. Center is 300mm."},
                "z": {"type": "number", "description": "Z position in mm."},
                "e": {"type": "number", "description": "Extrusion value."},
                "feedrate": {"type": "integer", "description": "Movement speed. Defaults to 2000."},
                "mode": {"type": "string", "description": "G0 for fast move, G1 for linear move. Defaults to G1."},
                "wait": {"type": "boolean", "description": "Whether to wait for the move to complete. Defaults to true."}
            },
            "required": [],
        },
    },
    {
        "name": "dexarm_get_current_position",
        "description": "Get the current position of the Dexarm (X, Y, Z, E and theta angles A, B, C).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dexarm_set_work_origin",
        "description": "Set the current Dexarm position as the new work origin (0, 0, 0, 0).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dexarm_set_module_type",
        "description": "Set the Dexarm end effector module type. 0=Pen holder, 1=Laser engraving, 2=Pneumatic, 3=3D printing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "module_type": {
                    "type": "integer",
                    "description": "Module type: 0=Pen, 1=Laser, 2=Pneumatic, 3=3D printing."
                }
            },
            "required": ["module_type"],
        },
    },
    {
        "name": "dexarm_get_module_type",
        "description": "Get the current Dexarm end effector module type (PEN, LASER, PUMP, or 3D).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "dexarm_set_acceleration",
        "description": "Set the Dexarm acceleration values for printing, travel, and retract moves.",
        "input_schema": {
            "type": "object",
            "properties": {
                "acceleration": {"type": "integer", "description": "Printing acceleration."},
                "travel_acceleration": {"type": "integer", "description": "Travel acceleration."},
                "retract_acceleration": {"type": "integer", "description": "Retract acceleration. Defaults to 60."}
            },
            "required": ["acceleration", "travel_acceleration"],
        },
    },
    {
        "name": "dexarm_delay_ms",
        "description": "Pause the Dexarm command queue for a specified number of milliseconds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "integer", "description": "Delay time in milliseconds."}
            },
            "required": ["value"],
        },
    },
    {
        "name": "dexarm_delay_s",
        "description": "Pause the Dexarm command queue for a specified number of seconds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "value": {"type": "integer", "description": "Delay time in seconds."}
            },
            "required": ["value"],
        },
    },
    {
        "name": "dexarm_soft_gripper_pick",
        "description": "Close the Dexarm soft gripper to pick up an object.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_soft_gripper_place",
        "description": "Wide-open the Dexarm soft gripper to place an object.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_soft_gripper_nature",
        "description": "Release the Dexarm soft gripper to its natural state.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_soft_gripper_stop",
        "description": "Stop the Dexarm soft gripper.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_air_picker_pick",
        "description": "Use the Dexarm air picker to pick up an object.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_air_picker_place",
        "description": "Use the Dexarm air picker to release an object.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_air_picker_nature",
        "description": "Release the Dexarm air picker to its natural state.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_air_picker_stop",
        "description": "Stop the Dexarm air picker.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_laser_on",
        "description": "Turn on the Dexarm laser module with specified power (1-255).",
        "input_schema": {
            "type": "object",
            "properties": {
                "power": {"type": "integer", "description": "Laser power, range 1-255. Defaults to 0."}
            },
            "required": [],
        },
    },
    {
        "name": "dexarm_laser_off",
        "description": "Turn off the Dexarm laser module.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_conveyor_belt_forward",
        "description": "Move the Dexarm conveyor belt forward at the given speed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "speed": {"type": "integer", "description": "Belt speed. Defaults to 0."}
            },
            "required": [],
        },
    },
    {
        "name": "dexarm_conveyor_belt_backward",
        "description": "Move the Dexarm conveyor belt backward at the given speed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "speed": {"type": "integer", "description": "Belt speed. Defaults to 0."}
            },
            "required": [],
        },
    },
    {
        "name": "dexarm_conveyor_belt_stop",
        "description": "Stop the Dexarm conveyor belt.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_sliding_rail_init",
        "description": "Initialize the Dexarm sliding rail.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "dexarm_send_raw_gcode",
        "description": "Send a raw G-code command string to the Dexarm. Use this for any command not covered by the other tools.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gcode": {"type": "string", "description": "The G-code command to send."},
                "wait": {"type": "boolean", "description": "Wait for 'ok' response. Defaults to true."}
            },
            "required": ["gcode"],
        },
    },
    {
        "name": "dexarm_clicking",
        "description": "Quickly click at a position using the Dexarm. Fast-moves above the target, presses down to z, then immediately releases upward by z_diff. All moves use G0 (fast mode). Always releases before any lateral movement.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "X position in mm."},
                "y": {"type": "number", "description": "Y position in mm."},
                "z": {"type": "number", "description": "Z press depth in mm (e.g. -58 for keyboard, -55 for mouse)."},
                "z_diff": {"type": "number", "description": "How far to release upward after pressing, in mm. Defaults to 5."}
            },
            "required": ["x", "y", "z"],
        },
    },
]

DEXARM_TOOL_FUNCTIONS = {
    "dexarm_board_check": dexarm_board_check,
    "dexarm_connect": dexarm_connect,
    "dexarm_disconnect": dexarm_disconnect,
    "dexarm_go_home": dexarm_go_home,
    "dexarm_move_to": dexarm_move_to,
    "dexarm_get_current_position": dexarm_get_current_position,
    "dexarm_set_work_origin": dexarm_set_work_origin,
    "dexarm_set_module_type": dexarm_set_module_type,
    "dexarm_get_module_type": dexarm_get_module_type,
    "dexarm_set_acceleration": dexarm_set_acceleration,
    "dexarm_delay_ms": dexarm_delay_ms,
    "dexarm_delay_s": dexarm_delay_s,
    "dexarm_soft_gripper_pick": dexarm_soft_gripper_pick,
    "dexarm_soft_gripper_place": dexarm_soft_gripper_place,
    "dexarm_soft_gripper_nature": dexarm_soft_gripper_nature,
    "dexarm_soft_gripper_stop": dexarm_soft_gripper_stop,
    "dexarm_air_picker_pick": dexarm_air_picker_pick,
    "dexarm_air_picker_place": dexarm_air_picker_place,
    "dexarm_air_picker_nature": dexarm_air_picker_nature,
    "dexarm_air_picker_stop": dexarm_air_picker_stop,
    "dexarm_laser_on": dexarm_laser_on,
    "dexarm_laser_off": dexarm_laser_off,
    "dexarm_conveyor_belt_forward": dexarm_conveyor_belt_forward,
    "dexarm_conveyor_belt_backward": dexarm_conveyor_belt_backward,
    "dexarm_conveyor_belt_stop": dexarm_conveyor_belt_stop,
    "dexarm_sliding_rail_init": dexarm_sliding_rail_init,
    "dexarm_send_raw_gcode": dexarm_send_raw_gcode,
    "dexarm_clicking": dexarm_clicking,
}
