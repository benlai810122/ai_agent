---
name: isst-driver-install
description: >
  Use when a test requires installing, uninstalling, or using a driver (such as an
  ISST driver or Bluetooth driver), including version comparison before install.
---

# ISST / Driver Installation

Driver files are located under the `test_assets/driver` subfolder inside the project
root. Always look for driver packages in that folder before attempting any driver
installation or referencing driver paths.

## Correct install workflow (follow in this exact order)
To install a new ISST driver, always perform these steps in order:

1. **Uninstall the current ISST driver** — `uninstall_all_isst_drivers`.
2. **Install the target ISST driver** — `install_all_isst_drivers` with the target
   version's driver folder (see "Installing" below).
3. **Reboot the laptop** — `reboot_laptop`.
4. **After reboot, verify the version** — `get_isst_driver_version` and confirm it now
   matches the target version.

For any "is the driver installed / what version" status check, use
`get_isst_driver_version` — it returns `installed`, `version`, `versions`, and
`packages_count`. There is no separate status function.

## Choosing the driver package
- Prefer the driver located inside the `Production` subfolder.
- If `Production` does not exist or is empty, fall back to the driver inside the
  `QS_Cert` subfolder.

## Version policy
Always uninstall the current ISST driver before installing a new one — regardless of
whether the target version is lower, equal, or higher than the installed version. Use
`uninstall_all_isst_drivers` to remove the installed ISST driver package(s).

## Installing
When installing a driver version, install ALL `.inf` files found inside that
version's driver folder.

- **Preferred:** call `install_all_isst_drivers` and pass the version's driver
  folder (e.g. the `Production` or `QS_Cert` folder, or its `Drivers` subfolder).
  It automatically discovers and installs **every** `.inf` under that folder —
  including nested `Extensions` INFs — so nothing is missed. Do NOT enumerate the
  INF names by hand; that is error-prone and easily leaves files out.

## Expected non-fatal INF failures
Some INFs in a package cannot be installed standalone and will always report a
failure. The clearest example is `IntelMvaExtension.inf`, an **`Class = Extension`**
INF that only attaches to an already-matched primary device. This is why a package
of 13 INFs typically reports `12/13 installed` — the missing one is the extension.

`install_all_isst_drivers` treats these as **non-fatal by default**: any INF that
declares `Class = Extension` (or is listed in `ignore_infs`) is recorded with
outcome `ignored` and does **not** fail the overall install. The result includes
`installed`, `ignored`, `failed`, `ignored_infs`, and `failed_infs` so you can tell
a real failure from an expected extension skip. Do not treat `12/13` as a failure
when the only missing INF is the extension.

\