---
name: bt-driver-install
description: >
  Use when a test requires installing, uninstalling, or checking the Intel Bluetooth
  (IBT) driver, including version comparison before install.
---

# Bluetooth (Intel IBT) Driver Installation

Bluetooth driver packages are located under the `test_assets/driver/bt_driver`
subfolder inside the project root. Always look there before installing or referencing
Bluetooth driver paths.

## Correct install workflow (follow in this exact order)
1. **Uninstall ALL old Bluetooth drivers first** — `uninstall_bluetooth_driver`. Always
   remove the existing driver before installing a new one, regardless of whether the
   target version is lower, equal, or higher.
2. **Install the target Bluetooth driver** — `install_bluetooth_driver` with the target
   version's driver folder (see "Installing" below).
3. **Reboot the laptop** — `reboot_laptop`. A Bluetooth driver change only takes full
   effect after a reboot.
4. **After reboot, verify** — confirm both the version and that Bluetooth still works:
   - `get_bluetooth_driver_version` — confirm `version` now matches the target.
   - `check_bluetooth_adapter_status` — confirm the adapter is present and enabled.
   - Optionally `scan_bluetooth_devices` to confirm the radio actually functions.

For any "is the driver installed / what version" status check, use
`get_bluetooth_driver_version` — it returns `installed`, `version`, `versions`, and
`packages_count`. There is no separate status function.

## Installing
Call `install_bluetooth_driver` and pass the target version's driver folder (the
extracted package folder or its `INF_INSTALL` subtree). It installs a **single**
transport INF chosen from the present Bluetooth radio's hardware ID bus prefix:

- USB radio (`USB\VID_8087...`) — installs `ibtusb.inf`.
- PCIe radio (`PCI\VEN_8086...`) — installs `ibtpci.inf`.

If no present Intel Bluetooth radio is found on either bus, the call returns
`not_found`. For `ibtusb`, the correct per-module "peak" profile folder is chosen
automatically. Confirm the install actually bound to the device (the result output
shows `installed on device: USB\...` or `PCI\...`); an INF that binds to no device did
not take effect.

## Uninstalling
`uninstall_bluetooth_driver` removes every Intel Bluetooth package from the driver
store and requires Administrator privileges.

## Reboot handling
Both install and uninstall may return `reboot_required: true` (pnputil codes
3010/1641). This is a SUCCESS, not a failure — Windows just needs a reboot to finish.
When `reboot_required` is true you MUST reboot before reinstalling, otherwise the
reinstall runs against a half-removed driver state.

## Expected non-fatal results
- A package whose device instance is no longer in the hardware tree
  (`SPAPI_E_NO_SUCH_DEVINST`, `0xE000020B`) is a stale/orphaned store entry. On
  uninstall it is reported as a **warning** (in `warnings`), not a failure.
- On a USB install, an INF added to the store but matching no present device is
  recorded as `ignored`, not `failed`. Only `failed_infs` indicate real failures.
