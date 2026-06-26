---
name: arduino-mouse-click
description: >
  Use to trigger Arduino-based mouse click actions over serial, including
  immediate click and delayed click flows.
---

# Arduino Mouse Click Skill

This skill controls an Arduino board connected via serial port to perform
mouse click actions.

## Steps
1. Call `arduino_board_check` to find the board.
2. Call `arduino_serial_connect` to connect to its COM port.
3. Then either:
   - `arduino_mouse_click` for an immediate mouse click, or
   - `arduino_mouse_delay_click` to click after a specified delay (in seconds).
