---
name: dexarm-mouse-click
description: >
  Use when a task requires DexArm to perform physical mouse clicking at a target
  position. Includes safe connect, home, click, and cleanup flow.
---

# DexArm Mouse Click

## Connection
1. Call `dexarm_connect` to auto-detect the arm.
2. After power-on, always call `dexarm_go_home` before movement.

## Mouse Clicking
Use `dexarm_clicking` to perform a safe click sequence (move above -> press -> release):

```
dexarm_clicking(x=180, y=280, z=-54, z_diff=8)
```

## Optional Movement Controls
- Use `dexarm_move_to` to move to staging points.
- Keep movements in safe, reachable coordinates.
- If the arm returns `XY beyond limit`, adjust coordinates and retry.

## Timing
- `dexarm_delay_ms(value)` for millisecond pauses.
- `dexarm_delay_s(value)` for second pauses.

## Cleanup
Always call `dexarm_go_home` before `dexarm_disconnect`.
