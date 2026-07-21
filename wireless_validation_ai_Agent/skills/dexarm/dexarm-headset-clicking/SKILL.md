---
name: dexarm-headset-clicking
description: >
  Use when a task requires DexArm to press the physical headset power button.
  Includes safe connect, home, staged approach movement, press, and cleanup flow.
---

# DexArm Headset Clicking

## Connection
1. Call `dexarm_connect` to auto-detect the arm.
2. After power-on, always call `dexarm_go_home` before movement.

## Headset Power Button
Target location:

| Axis | Value |
|------|-------|
| X | 175 mm |
| Y | 160 mm |
| Z | 50 mm |

## Approach Sequence (mandatory)
Before pressing the button, always move axes in this exact order to avoid collisions:

1. Call `dexarm_go_home` first.
2. Then raise Z to 80 and call `dexarm_get_current_position` to confirm Z has actually reached 80 before continuing.
3. Only after Z is confirmed at 80, move to X=175, Y=160.

```
dexarm_go_home()
dexarm_move_to(z=80, mode="G0")
dexarm_get_current_position()  # verify position.z == 80 before next step
dexarm_move_to(x=175, y=160, mode="G0")
```

## Press the Power Button
Choose the press type based on the intended action:

- To start Bluetooth searching (pairing mode), long press the power button for 5 seconds.
- To power on/off the headset, use a quick click on the power button.

After reaching the staging position (X=175, Y=160, Z=80), use a quick click at the button (z_diff=8):

```
dexarm_clicking(x=175, y=160, z=50, z_diff=8)
```

For a long press (e.g. Bluetooth searching / pairing mode), press down, wait 5 seconds, then release:

```
dexarm_move_to(x=175, y=160, z=50, mode="G0")
dexarm_delay_s(5)
dexarm_move_to(x=175, y=160, z=80, mode="G0")
```

## Retract Sequence (mandatory)
When the dexarm is about to move back to another position or home, always move to the safe retract point first to avoid collisions:

```
dexarm_move_to(x=0, y=300, z=80, mode="G0")
```

Only after reaching X=0, Y=300, Z=80 should you continue to the next position or call `dexarm_go_home`.

## Timing
- `dexarm_delay_ms(value)` for millisecond pauses.
- `dexarm_delay_s(value)` for second pauses.

## Cleanup
Always move to the retract point (X=0, Y=300, Z=80) first, then call `dexarm_go_home` before `dexarm_disconnect`.
