import subprocess
import json


def get_audio_endpoints() -> dict:
    """Get all audio input and output endpoints on this computer, including their status and default device info."""
    try:
        # Get audio output (render) devices
        render_cmd = [
            'powershell', '-Command',
            'Get-PnpDevice -Class AudioEndpoint | Where-Object {$_.FriendlyName -ne $null} | Select-Object FriendlyName, Status, InstanceId | ConvertTo-Json'
        ]
        render_proc = subprocess.run(render_cmd, capture_output=True, text=True, timeout=15)

        endpoints = []
        if render_proc.stdout.strip():
            data = json.loads(render_proc.stdout)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                endpoints.append({
                    "name": item.get("FriendlyName", "Unknown"),
                    "status": item.get("Status", "Unknown"),
                    "instance_id": item.get("InstanceId", ""),
                    "enabled": item.get("Status", "").lower() == "ok",
                })

        # Get default audio devices via PowerShell
        default_cmd = [
            'powershell', '-Command',
            '''
Add-Type -TypeDefinition @"
using System.Runtime.InteropServices;
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int dataFlow, int stateMask, out System.IntPtr devices);
    int GetDefaultAudioEndpoint(int dataFlow, int role, out System.IntPtr device);
}
"@ -ErrorAction SilentlyContinue

# Fallback: use registry or PowerShell cmdlets
$outputDevice = Get-ItemProperty "HKCU:\\Software\\Microsoft\\Multimedia\\Sound Mapper" -ErrorAction SilentlyContinue
if ($outputDevice) {
    Write-Output "DefaultOutput=$($outputDevice.Playback)"
    Write-Output "DefaultInput=$($outputDevice.Record)"
}
'''
        ]
        default_proc = subprocess.run(default_cmd, capture_output=True, text=True, timeout=10)

        default_output = ""
        default_input = ""
        if default_proc.stdout.strip():
            for line in default_proc.stdout.strip().split("\n"):
                if line.startswith("DefaultOutput="):
                    default_output = line.split("=", 1)[1].strip()
                elif line.startswith("DefaultInput="):
                    default_input = line.split("=", 1)[1].strip()

        return {
            "status": "success",
            "endpoint_count": len(endpoints),
            "endpoints": endpoints,
            "default_output": default_output if default_output else "Could not determine",
            "default_input": default_input if default_input else "Could not determine",
        }
    except Exception as e:
        return {"error": str(e)}


def check_headset_endpoint(device_name: str = "") -> dict:
    """Check the audio endpoint status for a specific headset device. Shows if it's registered as input, output, or both."""
    try:
        if not device_name:
            return {"error": "Please provide a device_name to search for."}

        search_name = device_name.lower()

        # Get all audio endpoints
        cmd = [
            'powershell', '-Command',
            'Get-PnpDevice -Class AudioEndpoint | Where-Object {$_.FriendlyName -ne $null} | Select-Object FriendlyName, Status, InstanceId | ConvertTo-Json'
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

        matched = []
        if proc.stdout.strip():
            data = json.loads(proc.stdout)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                friendly_name = item.get("FriendlyName", "")
                if search_name in friendly_name.lower():
                    instance_id = item.get("InstanceId", "")
                    # Determine direction from instance ID or name
                    is_output = "render" in instance_id.lower() or "speaker" in friendly_name.lower() or "headphone" in friendly_name.lower()
                    is_input = "capture" in instance_id.lower() or "microphone" in friendly_name.lower() or "mic" in friendly_name.lower()
                    if not is_output and not is_input:
                        # Default: check SWD path for direction hints
                        is_output = ".0.0.0" in instance_id or "render" in instance_id.lower()
                        is_input = ".0.1.0" in instance_id or "capture" in instance_id.lower()
                    matched.append({
                        "name": friendly_name,
                        "status": item.get("Status", "Unknown"),
                        "enabled": item.get("Status", "").lower() == "ok",
                        "instance_id": instance_id,
                        "direction": "input" if is_input else ("output" if is_output else "unknown"),
                    })

        if not matched:
            return {
                "status": "not_found",
                "message": f"No audio endpoint found matching '{device_name}'. The headset may not be connected or recognized.",
                "search_name": device_name,
            }

        has_input = any(d["direction"] == "input" for d in matched)
        has_output = any(d["direction"] == "output" for d in matched)

        return {
            "status": "success",
            "device_name": device_name,
            "endpoints_found": len(matched),
            "has_input": has_input,
            "has_output": has_output,
            "endpoints": matched,
        }
    except Exception as e:
        return {"error": str(e)}


def get_audio_volume() -> dict:
    """Get the current system audio volume level and mute status."""
    try:
        cmd = [
            'powershell', '-Command',
            '''
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public class AudioHelper {
    [DllImport("winmm.dll")]
    public static extern int waveOutGetVolume(IntPtr hwo, out uint dwVolume);
}
"@ -ErrorAction SilentlyContinue

$vol = 0
[uint32]$rawVol = 0
[AudioHelper]::waveOutGetVolume([IntPtr]::Zero, [ref]$rawVol) | Out-Null
$left = $rawVol -band 0xFFFF
$right = ($rawVol -shr 16) -band 0xFFFF
$leftPct = [math]::Round($left / 65535 * 100)
$rightPct = [math]::Round($right / 65535 * 100)
Write-Output "LeftVolume=$leftPct"
Write-Output "RightVolume=$rightPct"
'''
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

        left_vol = 0
        right_vol = 0
        if proc.stdout.strip():
            for line in proc.stdout.strip().split("\n"):
                if line.startswith("LeftVolume="):
                    left_vol = int(line.split("=")[1].strip())
                elif line.startswith("RightVolume="):
                    right_vol = int(line.split("=")[1].strip())

        avg_vol = (left_vol + right_vol) // 2

        return {
            "status": "success",
            "volume_percent": avg_vol,
            "left_volume": left_vol,
            "right_volume": right_vol,
        }
    except Exception as e:
        return {"error": str(e)}


def test_audio_output(device_name: str = "") -> dict:
    """Test audio output by playing a short system beep sound. Optionally specify a device name to verify it's active."""
    try:
        import winsound
        # If a device name is given, verify it exists as an active output endpoint
        if device_name:
            endpoint_check = check_headset_endpoint(device_name)
            if endpoint_check.get("status") == "not_found":
                return {
                    "status": "device_not_found",
                    "message": f"Audio output device '{device_name}' not found. Cannot test.",
                }
            if not endpoint_check.get("has_output", False):
                return {
                    "status": "no_output_endpoint",
                    "message": f"'{device_name}' has no output endpoint registered.",
                    "endpoints": endpoint_check.get("endpoints", []),
                }

        # Play a test beep (frequency=1000Hz, duration=500ms)
        winsound.Beep(1000, 5000)
        return {
            "status": "success",
            "message": "Test beep played successfully (1000Hz, 500ms). Ask the user if they heard it.",
            "device_tested": device_name if device_name else "default output device",
        }
    except Exception as e:
        return {"error": str(e)}


def test_audio_input(duration: int = 3) -> dict:
    """Test audio input (microphone) by recording a short sample and checking if sound was captured."""
    try:
        import wave
        import tempfile
        import os

        temp_path = os.path.join(tempfile.gettempdir(), "agent_mic_test.wav")

        # Use PowerShell to record audio via Windows AudioRecord
        ps_script = f'''
Add-Type -AssemblyName System.Speech
$recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$recognizer.SetInputToDefaultAudioDevice()

# Use a simple recording approach with NAudio or ffmpeg fallback
$duration = {duration}

# Try using ffmpeg if available
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {{
    & ffmpeg -y -f dshow -i audio="@device_cm*" -t $duration -ac 1 -ar 16000 "{temp_path}" 2>&1 | Out-Null
    if (Test-Path "{temp_path}") {{
        $fileSize = (Get-Item "{temp_path}").Length
        Write-Output "RECORDED:size=$fileSize"
    }} else {{
        Write-Output "FAILED:ffmpeg recording failed"
    }}
}} else {{
    # Fallback: use PowerShell .NET audio capture
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class WinMM {{
    [DllImport("winmm.dll")]
    public static extern int mciSendString(string command, System.Text.StringBuilder returnValue, int returnLength, IntPtr hwndCallback);
}}
"@
    $sb = New-Object System.Text.StringBuilder 256
    [WinMM]::mciSendString("open new type waveaudio alias mic", $sb, 256, [IntPtr]::Zero) | Out-Null
    [WinMM]::mciSendString("record mic", $sb, 256, [IntPtr]::Zero) | Out-Null
    Start-Sleep -Seconds $duration
    [WinMM]::mciSendString("save mic `"{temp_path}`"", $sb, 256, [IntPtr]::Zero) | Out-Null
    [WinMM]::mciSendString("close mic", $sb, 256, [IntPtr]::Zero) | Out-Null

    if (Test-Path "{temp_path}") {{
        $fileSize = (Get-Item "{temp_path}").Length
        Write-Output "RECORDED:size=$fileSize"
    }} else {{
        Write-Output "FAILED:MCI recording failed"
    }}
}}
'''
        proc = subprocess.run(
            ['powershell', '-Command', ps_script],
            capture_output=True, text=True, timeout=duration + 15
        )

        output = proc.stdout.strip()
        if "RECORDED:" in output:
            size_str = output.split("size=")[1].strip()
            file_size = int(size_str)
            # A very small WAV file (just headers) means no audio was captured
            has_audio = file_size > 1000  # WAV header is ~44 bytes, data should be much more

            return {
                "status": "success",
                "has_audio": has_audio,
                "file_size_bytes": file_size,
                "duration_seconds": duration,
                "recording_path": temp_path,
                "message": "Microphone captured audio successfully." if has_audio else "Recording created but very little audio data captured. Microphone may be muted or not working.",
            }
        else:
            return {
                "status": "recording_failed",
                "message": "Could not record audio. No recording tool (ffmpeg/MCI) available or microphone not accessible.",
                "details": output if output else proc.stderr[:500],
            }
    except Exception as e:
        return {"error": str(e)}


def set_default_audio_device(device_name: str, direction: str = "output") -> dict:
    """Set the default audio input or output device by name. Requires the AudioDeviceCmdlets PowerShell module."""
    try:
        if direction not in ("input", "output"):
            return {"error": "direction must be 'input' or 'output'."}

        # First check if the device exists
        endpoint_check = check_headset_endpoint(device_name)
        if endpoint_check.get("status") == "not_found":
            return {
                "status": "device_not_found",
                "message": f"Device '{device_name}' not found as an audio endpoint.",
            }

        # Use nircmd or PowerShell AudioDeviceCmdlets to set default device
        # Try AudioDeviceCmdlets first
        device_type = "1" if direction == "output" else "2"
        ps_script = f'''
try {{
    Import-Module AudioDeviceCmdlets -ErrorAction Stop
    $devices = Get-AudioDevice -List | Where-Object {{ $_.Name -like "*{device_name}*" -and $_.Type -eq "{direction}" }}
    if ($devices) {{
        $dev = $devices[0]
        Set-AudioDevice -ID $dev.ID | Out-Null
        Write-Output "SUCCESS:Set default {direction} to $($dev.Name)"
    }} else {{
        Write-Output "NOTFOUND:No matching {direction} device"
    }}
}} catch {{
    # Fallback: try via registry or nircmd
    Write-Output "MODULE_MISSING:AudioDeviceCmdlets not installed. Install with: Install-Module AudioDeviceCmdlets"
}}
'''
        proc = subprocess.run(
            ['powershell', '-Command', ps_script],
            capture_output=True, text=True, timeout=15
        )

        output = proc.stdout.strip()
        if "SUCCESS:" in output:
            msg = output.split("SUCCESS:")[1].strip()
            return {"status": "success", "message": msg}
        elif "NOTFOUND:" in output:
            return {"status": "not_found", "message": output.split("NOTFOUND:")[1].strip()}
        elif "MODULE_MISSING:" in output:
            return {"status": "module_missing", "message": output.split("MODULE_MISSING:")[1].strip()}
        else:
            return {"status": "unknown", "output": output, "stderr": proc.stderr[:500]}
    except Exception as e:
        return {"error": str(e)}


# Map of function name -> callable
HEADSET_TOOL_FUNCTIONS = {
    "get_audio_endpoints": get_audio_endpoints,
    "check_headset_endpoint": check_headset_endpoint,
    "get_audio_volume": get_audio_volume,
    "test_audio_output": test_audio_output,
    "test_audio_input": test_audio_input,
    "set_default_audio_device": set_default_audio_device,
}

# Anthropic-compatible tool definitions
HEADSET_ANTHROPIC_TOOLS = [
    {
        "name": "get_audio_endpoints",
        "description": "Get all audio input and output endpoints on this computer, including their status and default device info.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "check_headset_endpoint",
        "description": "Check the audio endpoint status for a specific headset device. Shows if it's registered as input (microphone), output (speaker/headphone), or both.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string", "description": "The name (or partial name) of the headset device to check, e.g. 'Jabra' or 'PLT Focus'."}
            },
            "required": ["device_name"],
        },
    },
    {
        "name": "get_audio_volume",
        "description": "Get the current system audio volume level (left/right channels) as a percentage.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "test_audio_output",
        "description": "Test audio output by playing a short beep sound. Optionally specify a device name to verify it's an active output endpoint first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string", "description": "Optional name of the audio output device to verify before testing."}
            },
            "required": [],
        },
    },
    {
        "name": "test_audio_input",
        "description": "Test audio input (microphone) by recording a short sample and checking if sound was captured.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration": {"type": "integer", "description": "Recording duration in seconds. Defaults to 3."}
            },
            "required": [],
        },
    },
    {
        "name": "set_default_audio_device",
        "description": "Set the default audio input or output device by name. Requires the AudioDeviceCmdlets PowerShell module.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device_name": {"type": "string", "description": "The name (or partial name) of the audio device to set as default."},
                "direction": {"type": "string", "enum": ["input", "output"], "description": "Whether to set as default input (microphone) or output (speaker/headphone). Defaults to 'output'."}
            },
            "required": ["device_name"],
        },
    },
]
