---
name: bluetooth-validation
description: >
  Use when a test involves Bluetooth: turning the radio on/off, disconnecting or
  reconnecting a paired device, checking connection status
---

# Bluetooth Validation

## Turn the radio off and on
Use `set_bluetooth_radio_via_ui` with `turn_on=false`, then `turn_on=true`.

## Disconnect / reconnect a device
- Disconnect: `disconnect_bluetooth_via_ui` with the device name.
- Reconnect: `reconnect_bluetooth_via_ui` with the device name.
- Check status: `check_bluetooth_connection_status`.




