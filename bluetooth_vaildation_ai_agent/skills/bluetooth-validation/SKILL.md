---
name: bluetooth-validation
description: >
  Use when a test involves Bluetooth: turning the radio on/off, disconnecting or
  reconnecting a paired device, checking connection status, running Bluetooth 5
  verification (ibterverify), or checking the I2S clock source (hcitool).
---

# Bluetooth Validation

## Turn the radio off and on
Use `set_bluetooth_radio_via_ui` with `turn_on=false`, then `turn_on=true`.

## Disconnect / reconnect a device
- Disconnect: `disconnect_bluetooth_via_ui` with the device name.
- Reconnect: `reconnect_bluetooth_via_ui` with the device name.
- Check status: `check_bluetooth_connection_status`.

## Bluetooth 5 verification
Always use `ibterverify.exe` located in the `Utilities/ibterverify` folder under the
project root.

## I2S clock source check
Use `hcitool.exe` located at `Utilities/hcitool/x64/hcitool.exe` under the project root.
