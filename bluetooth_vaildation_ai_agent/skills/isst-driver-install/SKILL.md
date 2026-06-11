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
2. Compare the version to install against the currently installed version:
   - If the version to install is **LOWER**, first call `uninstall_isst_driver` to
     remove the current driver, then proceed with installation.
   - If the version to install is **EQUAL or HIGHER**, install directly without
     uninstalling first.

## Installing
When installing a driver version, install ALL `.inf` files found inside that
version's driver folder.
