---
name: teams-join
description: >
  Use when a task requires joining or leaving a Microsoft Teams meeting.
  Includes guidance for interpreting status and confirming call state.
---

# Microsoft Teams Join and Leave

## Scope
This skill is limited to joining or leaving a Teams meeting and confirming status using:
- `join_teams_meeting`
- `check_teams_call_status`
- `leave_teams_call`

## Core Rules
1. For joining, always call `join_teams_meeting(meeting_url=...)` with a full Teams URL.
2. After joining or leaving, verify state with `check_teams_call_status`.
3. Keep actions explicit and sequential: join -> verify -> leave -> verify.
4. If call-state is unclear, run `check_teams_call_status` again after 2-3 seconds.

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

## Recommended Sequence
1. `join_teams_meeting(meeting_url=...)`
2. `check_teams_call_status`
3. `leave_teams_call`
4. `check_teams_call_status`

## Leave Flow
1. Call `leave_teams_call`.
2. Wait 2-3 seconds.
3. Call `check_teams_call_status` and confirm `call_active=false`.

## Failure Handling
If `join_teams_meeting` returns an error:
1. Ensure the URL is a valid Teams meeting URL.
2. Retry once after a short delay.
3. If still failing, run `check_teams_call_status` to capture current Teams state.

If `leave_teams_call` returns an error:
1. Re-run `check_teams_call_status` to verify if call already ended.
2. Retry leave once after a short delay.

If `check_teams_call_status` is uncertain:
1. Wait 2-3 seconds.
2. Re-run status check.
3. Use returned `signals` for debugging window-title based detection.

## Safety and Consistency
- Do not assume join success without status verification.
- Always verify final state after both join and leave.
- Prefer tool results over assumptions when reporting success/failure.
