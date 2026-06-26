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

## Examples
Type "HI":

```
dexarm_clicking(x=7, y=260, z=-58)
dexarm_clicking(x=43, y=280, z=-58)
```

Long press "A" for 3 seconds:

```
dexarm_move_to(x=-88, y=260, z=-53, mode="G0")
dexarm_move_to(x=-88, y=260, z=-58, mode="G0")
dexarm_delay_s(3)
dexarm_move_to(x=-88, y=260, z=-53, mode="G0")
```

## Cleanup
Always call `dexarm_go_home` before `dexarm_disconnect`.
