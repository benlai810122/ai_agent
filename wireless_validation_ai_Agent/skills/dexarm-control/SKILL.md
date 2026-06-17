---
name: dexarm-control
description: >
  Use when a task involves controlling the Rotrics DexArm robotic arm: connecting,
  homing, moving to positions, getting current position, or setting the work origin.
  Covers cartesian movement within the arm's workspace limits.
---

# DexArm Movement Control

## Connection
1. Call `dexarm_connect` — auto-detects the arm via hardware ID `VID:PID=0483:5740`.
   No need to specify a port unless multiple arms are connected.
2. After power-on, always call `dexarm_go_home` before any movement.

## Coordinate System
- The DexArm uses a **cartesian coordinate system** (X, Y, Z in millimeters).
- **X axis**: left/right, range roughly −300 to 300 mm (0 = center).
- **Y axis**: forward/back from base, range roughly 180 to 400 mm (home ≈ 300 mm).
- **Z axis**: vertical, range roughly −100 to 0 mm (0 = home height, negative = downward).
- The workspace is **arc-shaped** (SCARA arm), so not all X/Y combinations are reachable.
  Extreme X values require Y closer to 300 mm.

## Moving
- Use `dexarm_move_to` with any combination of `x`, `y`, `z` parameters.
- Omitted axes stay at their current position.
- `mode`: `G1` (default) for smooth linear movement, `G0` for fast movement.
- `feedrate`: movement speed, default 2000. Lower = slower and more precise.
- `wait`: set to `true` (default) to block until movement completes.
- If a position is out of range, the arm responds with "XY beyond limit" — the move is
  rejected and the arm stays in place. No damage occurs.

## Common Movement Patterns

### Move to a specific point
```
dexarm_move_to(x=0, y=300, z=0)
```

### Move down to a surface
```
dexarm_move_to(z=-50)          # lower the arm 50mm
```

### Move in a square pattern
```
dexarm_move_to(x=-50, y=280, z=0)
dexarm_move_to(x=50, y=280, z=0)
dexarm_move_to(x=50, y=350, z=0)
dexarm_move_to(x=-50, y=350, z=0)
```

### Fast move (G0) then precise move (G1)
```
dexarm_move_to(x=0, y=250, z=0, mode="G0")     # fast approach
dexarm_move_to(z=-30, feedrate=500)              # slow descent
```

### Mouse clicking
Press down then quickly release to simulate a physical mouse click:
```
dexarm_clicking(x=180, y=280, z=-54, z_diff=8)
```

### Keyboard clicking
Type on a physical keyboard by pressing keys with the arm. Use `dexarm_clicking`
for each key press — it automatically moves above, presses down, and releases upward.
Never move laterally while still pressed down.

**CRITICAL RULES:**
1. Use `dexarm_clicking(x, y, z=-58, z_diff=8)` for each normal key press.
2. For long press: use `dexarm_move_to` to press down, `dexarm_delay_s(3)`, then release with G0.
3. The `dexarm_clicking` function handles the move-above → press → release sequence safely.

**Key layout** (all keys at z=-58 press depth, z_diff=8):

| Key | X | Y |
|-----|------|-----|
| 1 | -100 | 300 |
| 2 | -81 | 300 |
| 3 | -62 | 300 |
| 4 | -43 | 300 |
| 5 | -24 | 300 |
| 6 | -5 | 300 |
| 7 | 14 | 300 |
| 8 | 33 | 300 |
| 9 | 52 | 300 |
| Q | -90 | 280 |
| W | -71 | 280 |
| E | -52 | 280 |
| R | -33 | 280 |
| T | -14 | 280 |
| Y | 5 | 280 |
| U | 24 | 280 |
| I | 43 | 280 |
| O | 62 | 280 |
| A | -88 | 260 |
| S | -69 | 260 |
| D | -50 | 260 |
| F | -31 | 260 |
| G | -12 | 260 |
| H | 7 | 260 |
| J | 26 | 260 |
| K | 45 | 260 |
| L | 64 | 260 |
| Z | -80 | 240 |
| X | -61 | 240 |
| C | -42 | 240 |
| V | -23 | 240 |
| B | -4 | 240 |
| N | 15 | 240 |
| M | 34 | 240 |
| SPACE | 0 | 220 |

**Example — type "HI":**
```
dexarm_clicking(x=7, y=260, z=-58)      # click H
dexarm_clicking(x=43, y=280, z=-58)     # click I
```

**Example — long press "A":**
```
dexarm_move_to(x=-88, y=260, z=-53, mode="G0")  # move above A
dexarm_move_to(x=-88, y=260, z=-58, mode="G0")  # press A
dexarm_delay_s(3)                                # hold for 3 seconds
dexarm_move_to(x=-88, y=260, z=-53, mode="G0")  # release A
```

## Position Queries
- `dexarm_get_current_position` returns X, Y, Z, E and theta angles A, B, C.
- `dexarm_set_work_origin` resets the current position to (0, 0, 0, 0) — useful for
  relative movements from a calibrated point.

## Timing
- `dexarm_delay_ms(value)` — pause in milliseconds (executed on the arm).
- `dexarm_delay_s(value)` — pause in seconds (executed on the arm).

## Cleanup
Always call `dexarm_go_home` before `dexarm_disconnect` to park the arm safely.
