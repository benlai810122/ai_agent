---
name: teams-control
description: >
  Use when a task involves Microsoft Teams call operations: joining a meeting by URL,
  checking call status, muting/unmuting microphone, toggling camera, and leaving a call.
  Includes guidance for interpreting vision-click outputs and Teams call-state signals.
---

# Microsoft Teams Call Control

## Scope
This skill controls Teams call actions through the available Teams tools:
- `join_teams_meeting`
- `check_teams_call_status`
- `mute_teams_microphone`
- `unmute_teams_microphone`
- `toggle_teams_camera`
- `leave_teams_call`

## Core Rules
1. For joining, always call `join_teams_meeting(meeting_url=...)` with a full Teams URL.
2. After joining or leaving, always verify state with `check_teams_call_status`.
3. Keep actions explicit and sequential: join -> verify -> control -> verify -> leave.
4. If a call-state result is unclear, run `check_teams_call_status` again after 2-3 seconds.

## Join Flow
1. Call `join_teams_meeting(meeting_url=<teams_link>)`.
2. Inspect result:
   - `status == success` and `method == vision_click` means the agent found and clicked Join now.
   - `vision` includes detected coordinates/confidence.
   - `click` includes cursor start/end/target positions.
3. Immediately call `check_teams_call_status` to confirm `call_active`.

## Call Status Interpretation
`check_teams_call_status` returns:
- `teams_running`: Teams app process is running.
- `call_active`: inferred active-call state.
- `signals`: diagnostic fields used for call detection.

Interpretation:
- `teams_running=true` + `call_active=true`: active call confirmed.
- `teams_running=true` + `call_active=false`: not in an active call (or state not detected yet).
- `teams_running=false`: Teams is closed/not running.

## In-call Controls
### Mute microphone
Call `mute_teams_microphone`.
- Sends `Ctrl+Shift+M` to Teams.

### Unmute microphone
Call `unmute_teams_microphone`.
- Sends `Ctrl+Shift+M` to Teams.

### Toggle camera
Call `toggle_teams_camera`.
- Sends `Ctrl+Shift+O`.
- Call again to switch back.

## Leave Call
1. Call `leave_teams_call`.
2. Wait 2-3 seconds.
3. Call `check_teams_call_status` and confirm `call_active=false`.

## Recommended End-to-end Sequence
1. `join_teams_meeting(meeting_url=...)`
2. `check_teams_call_status`
3. `mute_teams_microphone`
4. `unmute_teams_microphone`
5. `toggle_teams_camera`
6. `toggle_teams_camera`
7. `leave_teams_call`
8. `check_teams_call_status`

## Failure Handling
If `join_teams_meeting` returns an error:
1. Ensure the URL is a valid Teams meeting URL.
2. Retry once after a short delay.
3. If still failing, run `check_teams_call_status` to capture current Teams state.

If `check_teams_call_status` is uncertain:
1. Wait 2-3 seconds.
2. Re-run status check.
3. Use returned `signals` for debugging window-title based detection.

## Safety and Consistency
- Do not chain multiple hotkey actions without short delays between them.
- Always verify final state after join and leave.
- Prefer tool results over assumptions when reporting success/failure.
