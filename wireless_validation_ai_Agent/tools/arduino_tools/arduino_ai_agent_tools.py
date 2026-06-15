import serial
import serial.tools.list_ports
from enum import Enum


class ArduinoCommand(Enum):
    MOUSE_CLICK = "3"
    MOUSE_DELAY_CLICK = "4"


_serial_connection: serial.Serial = None



def arduino_board_check(target_port_desc: str = "USB-SERIAL CH340") -> dict:
    '''
    Check if an Arduino board is connected by searching for a matching COM port description.
    '''
    try:
        ports = serial.tools.list_ports.comports()
        for port, desc, hwid in ports:
            if target_port_desc in desc:
                return {"status": "success", "found": True, "port": port, "description": desc}
        return {"status": "success", "found": False, "message": f"No Arduino board matching '{target_port_desc}' found."}
    except Exception as e:
        return {"error": str(e)}


def arduino_serial_connect(port: str, baud_rate: int = 115200, timeout: int = 5) -> dict:
    '''
    Connect to an Arduino board via serial port.
    '''
    global _serial_connection
    try:
        if _serial_connection and _serial_connection.is_open:
            _serial_connection.close()
        _serial_connection = serial.Serial(port, baud_rate, timeout=timeout)
        if _serial_connection.is_open:
            return {"status": "success", "port": port, "baud_rate": baud_rate, "message": f"Connected to {port} at {baud_rate} baud."}
        else:
            return {"status": "failure", "message": f"Failed to open serial port {port}."}
    except Exception as e:
        return {"error": str(e)}


def arduino_send_command(command: str, read_timeout: float = 2.0) -> dict:
    '''
    Send a command string to the connected Arduino via serial and return the response.
    '''
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call arduino_serial_connect first."}
        _serial_connection.write((command + "\n").encode("utf-8"))
        _serial_connection.flush()
        import time
        time.sleep(read_timeout)
        # TBC: Arduino board currently does not respond after receiving command
        # response = _serial_connection.read_all().decode("utf-8", errors="replace").strip()
        return {"status": "success", "command": command}
    except Exception as e:
        return {"error": str(e)}


def arduino_mouse_click() -> dict:
    '''
    Send a mouse click command to the Arduino.
    '''
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call arduino_serial_connect first."}
        cmd = str.encode(ArduinoCommand.MOUSE_CLICK.value) + b'\n'
        _serial_connection.write(cmd)
        _serial_connection.flush()
        import time
        time.sleep(1.0)
        # TBC: Arduino board currently does not respond after receiving command
        # response = _serial_connection.read_all().decode("utf-8", errors="replace").strip()
        return {"status": "success", "action": "mouse_click"}
    except Exception as e:
        return {"error": str(e)}


def arduino_mouse_delay_click(delay_time: int = 120) -> dict:
    '''
    Send a delayed mouse click command to the Arduino.
    '''
    global _serial_connection
    try:
        if not _serial_connection or not _serial_connection.is_open:
            return {"error": "No active serial connection. Call arduino_serial_connect first."}
        cmd = str.encode(f"{ArduinoCommand.MOUSE_DELAY_CLICK.value}:{delay_time}") + b'\n'
        _serial_connection.write(cmd)
        _serial_connection.flush()
        import time
        time.sleep(1.0)
        # TBC: Arduino board currently does not respond after receiving command
        # response = _serial_connection.read_all().decode("utf-8", errors="replace").strip()
        return {"status": "success", "action": "mouse_delay_click", "delay_time": delay_time}
    except Exception as e:
        return {"error": str(e)}


ARDUINO_ANTHROPIC_TOOLS = [
    {
        "name": "arduino_board_check",
        "description": "Check if an Arduino board is currently connected to the system by searching for a matching COM port description. Returns the port name if found.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target_port_desc": {
                    "type": "string",
                    "description": "A substring to match against COM port descriptions (e.g. 'Arduino', 'CH340', 'USB-SERIAL'). Defaults to 'USB-SERIAL CH340'."
                }
            },
            "required": [],
        },
    },
    {
        "name": "arduino_serial_connect",
        "description": "Connect to an Arduino board via a specific serial COM port. Returns connection status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "string",
                    "description": "The COM port to connect to (e.g. 'COM3', 'COM5')."
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
            "required": ["port"],
        },
    },
    {
        "name": "arduino_send_command",
        "description": "Send a command string to the connected Arduino via serial port and return the response. Requires an active connection from arduino_serial_connect.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command string to send to the Arduino."
                },
                "read_timeout": {
                    "type": "number",
                    "description": "Time in seconds to wait for the Arduino response. Defaults to 2.0."
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "arduino_mouse_click",
        "description": "Send a mouse click command to the Arduino. Triggers an immediate mouse click action. Requires an active serial connection.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "arduino_mouse_delay_click",
        "description": "Send a delayed mouse click command to the Arduino. Triggers a mouse click after a specified delay. Requires an active serial connection.",
        "input_schema": {
            "type": "object",
            "properties": {
                "delay_time": {
                    "type": "integer",
                    "description": "Delay time in seconds before the mouse click. Defaults to 120."
                }
            },
            "required": [],
        },
    },
]

ARDUINO_TOOL_FUNCTIONS = {
    "arduino_board_check": arduino_board_check,
    "arduino_serial_connect": arduino_serial_connect,
    "arduino_send_command": arduino_send_command,
    "arduino_mouse_click": arduino_mouse_click,
    "arduino_mouse_delay_click": arduino_mouse_delay_click,
}
