---
name: power-state-s3
description: >
  Use when a task requires putting the laptop into S3 sleep mode and waking it
  up at a specific time using a physical mouse click. The agent first detects
  whether an Arduino or a DexArm is connected and uses that device to trigger
  the wake-up click. If neither device is detected, the test item fails.
---

# Power State S3 (sleep + timed wake-up)

Use this skill whenever a step requires the laptop to enter S3 sleep and then
resume automatically at a specific time. Because the laptop cannot send commands
while it is asleep, the wake-up is performed by a physical mouse click from an
external device whose delay timer is armed BEFORE the laptop sleeps.

## Wake-up device detection (do this FIRST)
The laptop can be woken by one of two devices. Detect which one is currently
connected before doing anything else:

1. **Arduino** — call `arduino_board_check`. If a board is found, this is the
   wake-up device.
2. **DexArm** — call `dexarm_connect` (auto-detects the arm). If the arm
   connects, this is the wake-up device.

Rules:
- If **only one** device is detected, use that device.
- If **both** are detected, prefer the Arduino (its onboard timer keeps running
  while the laptop sleeps, making it the most reliable wake source).
- If **neither** device is detected, the wake-up cannot be performed —
  **mark the test item as FAIL** and stop. Do not put the laptop to sleep with
  no way to wake it.

## Flow

### Option 1 — Arduino mouse clicking
1. `arduino_board_check` to find the board.
2. `arduino_serial_connect` to connect to its COM port.
3. Arm the delayed click BEFORE sleeping: `arduino_mouse_delay_click(delay_time=<seconds>)`.
   The Arduino runs this timer on its own hardware, so the click fires even
   while the laptop is in S3. Set `delay_time` to the number of seconds from now
   until the desired wake-up time.
4. Immediately call `go_to_s3` to put the laptop to sleep.
5. When the Arduino's timer elapses it clicks the mouse and the laptop wakes.

### Option 2 — DexArm mouse clicking
When the DexArm is the wake-up device, call the single **`power_state_s3_dexarm_wake`**
tool. It runs the whole flow (connect, home, stage above the mouse target, queue
a delayed **double click** non-blocking, put the laptop into S3, then home the
arm after wake) in one blocking call.

```
power_state_s3_dexarm_wake(delay_seconds=<seconds>)
```

- `delay_seconds` is the time from now until the desired wake-up time.
- Optional `x`, `y`, `z`, `z_diff` override the default click coordinates.

**IMPORTANT — do not do anything else while it runs.** This tool blocks until
the laptop has woken and the arm is homed. Do **not** call `go_to_s3` or any
DexArm tools separately, and do **not** start other steps until the tool
returns. The laptop is asleep for the whole duration, so the agent process is
suspended and will resume only after wake-up.

## Notes
- Always arm/prepare the wake-up device BEFORE calling `go_to_s3`.
- Compute the delay (Arduino `delay_time`, or the DexArm `G4 S<seconds>` dwell)
  from the current time to the requested wake-up time.
- For the DexArm, always send the dwell and move commands non-blocking
  (`wait=False`) so `go_to_s3` can run immediately.
- Never enter S3 if no wake-up device is available — that would leave the laptop
  unable to resume the validation flow.
- After the laptop wakes, confirm it is responsive before continuing the flow.
