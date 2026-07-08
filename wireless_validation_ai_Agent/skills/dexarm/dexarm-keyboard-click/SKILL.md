---
name: dexarm-keyboard-click
description: >
  Use when a task requires DexArm to type on a physical keyboard by pressing
  keys at calibrated coordinates.
---

# DexArm Keyboard Click

## Connection
1. Call `dexarm_connect` to auto-detect the arm.
2. After power-on, always call `dexarm_go_home` before movement.

## Keyboard Clicking Rules
1. Use `dexarm_clicking(x, y, z=-58, z_diff=8)` for each normal key press.
2. For long press: use `dexarm_move_to` to press down, wait with `dexarm_delay_s`, then release.
3. Never move laterally while still pressed down.

## Key Layout
All keys use `z=-58` press depth with `z_diff=8`.

| Key | X | Y |
|-----|------|-----|
| 1 | -110 | 315 |
| 2 | -91 | 315 |
| 3 | -72 | 315 |
| 4 | -53 | 315 |
| 5 | -34 | 315 |
| 6 | -15 | 315 |
| 7 | 4 | 315 |
| 8 | 23 | 315 |
| 9 | 42 | 315 |
| Q | -100 | 300 |
| W | -81 | 300 |
| E | -62 | 300 |
| R | -43 | 300 |
| T | -24 | 300 |
| Y | -5 | 300 |
| U | 14 | 300 |
| I | 33 | 300 |
| O | 52 | 300 |
| A | -95 | 280 |
| S | -76 | 280 |
| D | -57 | 280 |
| F | -38 | 280 |
| G | -19 | 280 |
| H | 0 | 280 |
| J | 19 | 280 |
| K | 38 | 280 |
| L | 57 | 280 |
| Z | -85 | 260 |
| X | -66 | 260 |
| C | -47 | 260 |
| V | -28 | 260 |
| B | -9 | 260 |
| N | 10 | 260 |
| M | 29 | 260 |
| SPACE | -10 | 240 |

## Examples
Type "HI":

```
dexarm_clicking(x=0, y=280, z=-58)
dexarm_clicking(x=33, y=300, z=-58)
```

Long press "A" for 3 seconds:

```
dexarm_move_to(x=-95, y=280, z=-53, mode="G0")
dexarm_move_to(x=-95, y=280, z=-58, mode="G0")
dexarm_delay_s(3)
dexarm_move_to(x=-95, y=280, z=-53, mode="G0")
```

## Cleanup
Always call `dexarm_go_home` before `dexarm_disconnect`.
