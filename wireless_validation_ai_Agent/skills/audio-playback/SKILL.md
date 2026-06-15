---
name: audio-playback
description: >
  Use for headset/audio validation: playing a tone, capturing a screenshot of the
  player, recording headset output, and analyzing audio quality. Covers any test
  that plays music or audio.
---

# Audio Playback Validation

Make sure system audio is NOT muted before and during playback checks.

When the user asks to play music or audio, use `open_local_file` to open the file
from the `test_assets/audio` subfolder inside the project root. If no specific track
is named, first call `list_directory` on the `test_assets/audio` folder to discover
available files, then open the first suitable one.

If the user does not specify a playback duration, use **10 seconds** as the default.

## Standard audio test steps (in order)
1. Play `test_1k_tone.mp3` from `test_assets/audio` using `open_local_file`.
2. While it is playing, capture a screenshot and analyze it for playback issues such
   as audio not playing or media player error codes.
3. While it is still playing, use `record_audio_output` to record the headset audio
   output and save the recording inside the current test run folder.
4. After recording finishes, stop playback using `close_media_player`.
5. Use `analyze_audio_file` on the recorded file to check audio quality (volume
   levels, clipping, silence, distortion).
6. Include both the screenshot analysis and the audio quality analysis results in
   the test report.
