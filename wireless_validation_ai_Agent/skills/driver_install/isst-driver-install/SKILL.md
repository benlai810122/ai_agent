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

## Choosing the driver package
- Prefer the driver located inside the `Production` subfolder.
- If `Production` does not exist or is empty, fall back to the driver inside the
  `QS_Cert` subfolder.

## Version comparison before installing
1. Always call `get_isst_driver_version` to check the currently installed version.
2. Regardless of whether the version to install is lower, equal, or higher than the
   currently installed version, always uninstall the current ISST driver first
   before installing the new version:
   - Call `uninstall_all_isst_drivers` (or `uninstall_isst_driver`) to remove the
     current driver, then proceed with installation.

## Installing
When installing a driver version, install ALL `.inf` files found inside that
version's driver folder.

- **Preferred:** call `install_all_isst_drivers` and pass the version's driver
  folder (e.g. the `Production` or `QS_Cert` folder, or its `Drivers` subfolder).
  It automatically discovers and installs **every** `.inf` under that folder —
  including nested `Extensions` INFs — so nothing is missed. Do NOT enumerate the
  INF names by hand; that is error-prone and easily leaves files out.
- Only fall back to calling `install_isst_driver` per file when you deliberately
  need to install a single specific `.inf`.
