import subprocess
import json
import os


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


def record_audio_output(save_path: str, duration: int = 10, device_name: str = "") -> dict:
    """Record system audio output (speaker/headset playback) to the specified WAV path.

    Uses the soundcard library for WASAPI loopback recording on Windows.
    """
    try:
        if not save_path or not isinstance(save_path, str):
            return {"error": "save_path is required."}

        if duration <= 0 or duration > 300:
            return {"error": "duration must be between 1 and 300 seconds."}

        # Optional headset verification before recording.
        if device_name:
            endpoint_check = check_headset_endpoint(device_name)
            if endpoint_check.get("status") == "not_found":
                return {
                    "status": "device_not_found",
                    "message": f"Audio output device '{device_name}' not found. Cannot record output.",
                }
            if not endpoint_check.get("has_output", False):
                return {
                    "status": "no_output_endpoint",
                    "message": f"'{device_name}' has no output endpoint registered.",
                    "endpoints": endpoint_check.get("endpoints", []),
                }

        abs_save_path = os.path.abspath(save_path)
        os.makedirs(os.path.dirname(abs_save_path), exist_ok=True)

        try:
            import soundcard as sc
            import numpy as np
            from scipy.io import wavfile
        except ImportError as ie:
            return {
                "status": "dependency_missing",
                "message": f"Required library not installed: {ie}. Run: pip install soundcard numpy scipy",
            }

        sample_rate = 48000

        # Try to find the target speaker by device_name, fall back to default speaker.
        target_speaker = None
        if device_name:
            search = device_name.lower()
            for spk in sc.all_speakers():
                if search in spk.name.lower():
                    target_speaker = spk
                    break

        if target_speaker is None:
            target_speaker = sc.default_speaker()

        if target_speaker is None:
            return {
                "status": "no_speaker",
                "message": "No audio output device found.",
            }

        # Record via loopback (captures what's playing through the target device)
        mic = sc.get_microphone(id=str(target_speaker.name), include_loopback=True)
        if mic is None:
            return {
                "status": "loopback_unavailable",
                "message": f"Could not open loopback device for '{target_speaker.name}'.",
            }

        num_frames = sample_rate * duration
        with mic.recorder(samplerate=sample_rate, channels=2) as recorder:
            audio_data = recorder.record(numframes=num_frames)

        # Convert float32 [-1.0, 1.0] to int16 for WAV
        audio_int16 = np.clip(audio_data * 32767, -32768, 32767).astype(np.int16)
        wavfile.write(abs_save_path, sample_rate, audio_int16)

        if not os.path.exists(abs_save_path):
            return {
                "status": "recording_failed",
                "message": "Recording finished but output file was not created.",
            }

        file_size = os.path.getsize(abs_save_path)
        return {
            "status": "success",
            "message": "Audio output recorded successfully via loopback.",
            "recording_path": abs_save_path,
            "duration_seconds": duration,
            "file_size_bytes": file_size,
            "sample_rate": sample_rate,
            "channels": 2,
            "device_tested": device_name if device_name else target_speaker.name,
        }
    except Exception as e:
        return {"error": str(e)}


def analyze_audio_file(file_path: str) -> dict:
    """Analyze a recorded audio file using ffprobe to extract audio quality metrics such as duration, codec, sample rate, channels, bit rate, and peak/mean volume levels."""
    try:
        if not file_path or not isinstance(file_path, str):
            return {"error": "file_path is required."}

        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return {"error": f"File not found: {abs_path}"}

        file_size = os.path.getsize(abs_path)
        if file_size == 0:
            return {"status": "empty_file", "message": "The audio file is empty (0 bytes). Recording may have failed.", "file_path": abs_path}

        # Check ffprobe availability
        ffprobe_check = subprocess.run(
            ['powershell', '-Command', 'Get-Command ffprobe -ErrorAction SilentlyContinue | Select-Object -First 1'],
            capture_output=True, text=True, timeout=10,
        )
        if not ffprobe_check.stdout.strip():
            return {"status": "ffprobe_missing", "message": "ffprobe is required to analyze audio files. Please install ffmpeg (includes ffprobe) and ensure it is in PATH."}

        # Get stream info (codec, sample rate, channels, bit rate, duration)
        probe_cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'a:0',
            '-show_entries', 'stream=codec_name,sample_rate,channels,bit_rate,duration',
            '-show_entries', 'format=duration,size,bit_rate',
            '-of', 'json',
            abs_path,
        ]
        probe_proc = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        if probe_proc.returncode != 0:
            return {"status": "probe_failed", "message": "ffprobe failed to read audio stream info.", "stderr": probe_proc.stderr[-500:]}

        probe_data = json.loads(probe_proc.stdout)
        stream = probe_data.get("streams", [{}])[0] if probe_data.get("streams") else {}
        fmt = probe_data.get("format", {})

        # Get volume stats (peak and mean) via ffmpeg volumedetect filter
        vol_cmd = [
            'ffmpeg', '-i', abs_path,
            '-af', 'volumedetect',
            '-f', 'null', '-',
        ]
        vol_proc = subprocess.run(vol_cmd, capture_output=True, text=True, timeout=60)
        import re as _re
        vol_stderr = vol_proc.stderr or ""
        mean_match = _re.search(r'mean_volume:\s*([\-\d.]+)\s*dB', vol_stderr)
        max_match = _re.search(r'max_volume:\s*([\-\d.]+)\s*dB', vol_stderr)

        mean_volume = float(mean_match.group(1)) if mean_match else None
        max_volume = float(max_match.group(1)) if max_match else None

        # Determine quality assessment
        issues = []
        duration_sec = float(stream.get("duration") or fmt.get("duration") or 0)
        if duration_sec < 1:
            issues.append("Recording duration is less than 1 second — may indicate a failed capture.")
        if mean_volume is not None and mean_volume < -60:
            issues.append(f"Mean volume is very low ({mean_volume} dB) — audio may be silent or nearly inaudible.")
        if max_volume is not None and max_volume >= 0:
            issues.append(f"Max volume is {max_volume} dB — audio is clipping (digital distortion).")
        if mean_volume is not None and max_volume is not None and (max_volume - mean_volume) < 1:
            issues.append("Very low dynamic range — possible constant-level noise or distortion.")

        quality = "PASS" if not issues else "FAIL"

        return {
            "status": "success",
            "file_path": abs_path,
            "file_size_bytes": file_size,
            "codec": stream.get("codec_name", "unknown"),
            "sample_rate": stream.get("sample_rate", "unknown"),
            "channels": stream.get("channels", "unknown"),
            "bit_rate": stream.get("bit_rate") or fmt.get("bit_rate", "unknown"),
            "duration_seconds": duration_sec,
            "mean_volume_dB": mean_volume,
            "max_volume_dB": max_volume,
            "quality": quality,
            "issues": issues if issues else ["No issues detected."],
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "message": "Audio analysis timed out."}
    except Exception as e:
        return {"error": str(e)}


# Map of function name -> callable
HEADSET_TOOL_FUNCTIONS = {
    "get_audio_endpoints": get_audio_endpoints,
    "check_headset_endpoint": check_headset_endpoint,
    "get_audio_volume": get_audio_volume,
    "test_audio_input": test_audio_input,
    "set_default_audio_device": set_default_audio_device,
    "record_audio_output": record_audio_output,
    "analyze_audio_file": analyze_audio_file,
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
    {
        "name": "record_audio_output",
        "description": "Record system audio output playback and save it to a specified path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "save_path": {"type": "string", "description": "Output file path (recommended .wav) for the recording."},
                "duration": {"type": "integer", "description": "Recording duration in seconds. Defaults to 10. Range: 1-300."},
                "device_name": {"type": "string", "description": "Optional headset name to verify output endpoint before recording."}
            },
            "required": ["save_path"],
        },
    },
    {
        "name": "analyze_audio_file",
        "description": "Analyze a recorded audio file for quality metrics including codec, sample rate, channels, duration, and volume levels (mean/peak). Returns a PASS/FAIL quality assessment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the audio file to analyze (WAV, MP3, etc.)."}
            },
            "required": ["file_path"],
        },
    },
]
