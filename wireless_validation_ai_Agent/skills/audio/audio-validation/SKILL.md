---
name: audio-validation
description: >
  Use for headset/audio validation: playing a tone, capturing a screenshot of the
  player, recording headset output, and analyzing audio quality. Covers any test
  that plays music or audio.
---

# Audio Validation Skill

Make sure system audio is NOT muted before and during playback checks.

When the user asks to play music or audio, use `open_local_file` to open the file
from the `test_assets/audio` subfolder inside the project root. If no specific track
is named, first call `list_directory` on the `test_assets/audio` folder to discover
available files, then open the first suitable one.

If the user does not specify a playback duration, use **10 seconds** as the default.

## Standard audio test steps (in order)

### Pre-flight: Verify headset is ready before any audio test
1. Call `check_bluetooth_connection_status` with the headset device name.
   - The result must show `"connected": true`. If the device is not connected:
     a. Call `reconnect_bluetooth_via_ui` with the device name.
     b. Add a `delay` of **5 seconds** after reconnect to allow the audio stack to register.
     c. Call `check_bluetooth_connection_status` again to confirm connection.
     d. If still not connected, fail the test and report the headset is unavailable.
2. Call `check_headset_endpoint` with the device name to verify the audio endpoints.
   - Both `has_input` and `has_output` must be `true` in the result.
   - If either endpoint is missing, add a `delay` of **3 seconds** and retry once.
   - If endpoints are still missing after the retry, fail the test and report the
     endpoint status.
3. Add a `delay` of **2 seconds** after confirming both endpoints before proceeding
   to playback. This ensures the audio stack is fully stable.

### Playback and recording
4. Play `test_1k_tone.mp3` from `test_assets/audio` using `open_local_file`.
5. Add a `delay` of **2 seconds** to allow the media player to open and start playback.
6. While it is playing, capture a screenshot and analyze it for playback issues such
   as audio not playing or media player error codes.
7. While it is still playing, use `record_audio_output` with the headset `device_name`
   to record its audio output and save the recording inside the current test run folder.
8. After recording finishes, add a `delay` of **2 seconds** to allow the file to be
   fully written to disk, then stop playback using `close_media_player`.
9. Use `analyze_audio_file` on the recorded file to check audio quality (volume
   levels, clipping, silence, distortion).
10. Include the connection status, endpoint status, screenshot analysis, and audio
    quality analysis results in the test report.
