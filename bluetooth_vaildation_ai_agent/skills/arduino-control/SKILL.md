---
name: arduino-control
description: >
  Use to control an Arduino board over serial to perform physical actions such as
  mouse clicking (immediate or delayed).
---

# Arduino Control

You can control an Arduino board connected via serial port to perform physical
actions such as mouse clicking.

## Steps
1. Call `arduino_board_check` to find the board.
2. Call `arduino_serial_connect` to connect to its COM port.
3. Then either:
   - `arduino_mouse_click` for an immediate mouse click, or
   - `arduino_mouse_delay_click` to click after a specified delay (in seconds).
