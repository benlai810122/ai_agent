---
name: bluetooth-validation
description: >
  Use when a test involves Bluetooth: turning the radio on/off, disconnecting or
  reconnecting a paired device, checking connection status
---

# Bluetooth Validation

## Turn the radio off and on
Use `set_bluetooth_radio_via_ui` with `turn_on=false`, then `turn_on=true`.

## Verify the Bluetooth radio/adapter is working
To confirm the laptop's Bluetooth function itself is healthy (e.g. after a reboot or after
toggling the radio off/on), use `check_bluetooth_adapter_status`. It takes no arguments.
Do NOT use `check_bluetooth_connection_status` for this — that function checks a specific
paired device and requires a `device_name` or `address`.

## Disconnect / reconnect a device
- Disconnect: `disconnect_bluetooth_via_ui` with the device name.
- Reconnect: `reconnect_bluetooth_via_ui` with the device name.
- Check a specific device's connection: `check_bluetooth_connection_status` with its `device_name`.




