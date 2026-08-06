import os
import re
import subprocess


def _extract_driver_version(text: str):
    """Pull the dotted version token (e.g. 20.40.12741.9) out of a pnputil value."""
    m = re.search(r"\d+\.\d+\.\d+\.\d+", text or "")
    return m.group(0) if m else None


def _version_key(version: str):
    """Numeric sort key so 20.40.12741.9 ranks above 3.1.2.6 (not lexicographically)."""
    return tuple(int(part) for part in version.split("."))


def _is_admin() -> bool:
    """Return True if the current process is running with Administrator rights."""
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


_NOT_ADMIN_HINT = (
    "The agent process is not running as Administrator, so pnputil cannot add or "
    "remove drivers (Access is denied). Relaunch the agent via 'Launch Agent.bat' "
    "and accept the UAC prompt (or start web_ui.py from an elevated terminal). "
    "Running 'python web_ui.py' from a normal terminal is NOT elevated."
)


def install_isst_driver(inf_path: str) -> dict:
    """Install the ISST driver from a .inf file using pnputil.

    Args:
        inf_path: The full path to the .inf driver file to install.

    Returns:
        A dict with status and details of the installation result.
    """
    try:
        if not inf_path or not inf_path.lower().endswith(".inf"):
            return {"error": "A valid .inf file path must be provided."}

        if not os.path.isfile(inf_path):
            return {"error": f"INF file not found: {inf_path}"}

        if not _is_admin():
            return {"status": "failed", "error": _NOT_ADMIN_HINT}

        # Use pnputil to add and install the driver
        result = subprocess.run(
            ["pnputil", "/add-driver", inf_path, "/install"],
            capture_output=True,
            text=True,
            timeout=120,
        )

        output = result.stdout.strip()
        err_output = result.stderr.strip()

        # pnputil returns 259 (ERROR_NO_MORE_ITEMS) when the package is already
        # present / up-to-date on the device and nothing new was added. The driver
        # is effectively installed, so treat that as success rather than a failure.
        already_present = (
            result.returncode == 259
            or "already exists" in output.lower()
            or "up-to-date" in output.lower()
        )

        if result.returncode == 0:
            return {
                "status": "success",
                "message": "ISST driver installed successfully.",
                "output": output,
            }
        elif already_present:
            return {
                "status": "success",
                "message": "ISST driver already present / up-to-date on the system.",
                "return_code": result.returncode,
                "output": output,
            }
        else:
            return {
                "status": "failed",
                "message": "ISST driver installation failed.",
                "return_code": result.returncode,
                "output": output,
                "error_output": err_output,
            }
    except subprocess.TimeoutExpired:
        return {"error": "Driver installation timed out after 120 seconds."}
    except Exception as e:
        return {"error": str(e)}


def install_all_isst_drivers(driver_folder: str, recursive: bool = True) -> dict:
    """Install EVERY ``.inf`` file found under a driver folder using pnputil.

    This is the reliable way to satisfy "install ALL .inf files" — instead of
    listing each INF name by hand (which is error-prone and easy to leave
    incomplete), point this at a version's driver folder and it discovers and
    installs every ``.inf`` automatically. By default it searches subfolders too,
    so both ``Drivers`` and ``Extensions`` INFs under the folder are included.

    Args:
        driver_folder: Path to the folder containing the ``.inf`` files (e.g. the
            version's ``Production`` or ``QS_Cert`` folder, or its ``Drivers``
            subfolder).
        recursive: When True (default), also install ``.inf`` files in nested
            subfolders (e.g. ``Extensions/OemExtensionInfs/...``).

    Returns:
        A dict with an overall status plus per-file installation results.
    """
    try:
        if not driver_folder or not isinstance(driver_folder, str):
            return {"error": "A valid driver_folder path must be provided."}

        if not os.path.isdir(driver_folder):
            return {"error": f"Driver folder not found: {driver_folder}"}

        if not _is_admin():
            return {"status": "failed", "error": _NOT_ADMIN_HINT}

        # Discover every .inf under the folder.
        inf_paths = []
        if recursive:
            for root, _dirs, files in os.walk(driver_folder):
                for name in files:
                    if name.lower().endswith(".inf"):
                        inf_paths.append(os.path.join(root, name))
        else:
            for name in os.listdir(driver_folder):
                full = os.path.join(driver_folder, name)
                if os.path.isfile(full) and name.lower().endswith(".inf"):
                    inf_paths.append(full)

        inf_paths.sort()

        if not inf_paths:
            return {
                "status": "not_found",
                "message": f"No .inf files found under '{driver_folder}'.",
                "driver_folder": driver_folder,
            }

        results = []
        installed = 0
        for inf_path in inf_paths:
            res = install_isst_driver(inf_path)
            ok = res.get("status") == "success"
            if ok:
                installed += 1
            results.append({
                "inf_path": inf_path,
                "inf_name": os.path.basename(inf_path),
                "status": res.get("status", "error" if res.get("error") else "unknown"),
                "message": res.get("message") or res.get("error", ""),
            })

        total = len(inf_paths)
        overall = "success" if installed == total else "failed"
        return {
            "status": overall,
            "message": f"Installed {installed}/{total} .inf file(s) from '{driver_folder}'.",
            "driver_folder": driver_folder,
            "total": total,
            "installed": installed,
            "results": results,
        }
    except Exception as e:
        return {"error": str(e)}


def _resolve_published_names(inf_name: str) -> list[str]:
    """Resolve one or more published oem*.inf names from a driver name.

    If inf_name is already a published name (e.g. 'oem123.inf'), return it as-is.
    If inf_name is an original name (e.g. 'IntcBTAu.inf'), enumerate all installed
    drivers and return every published name whose original INF matches.

    Args:
        inf_name: Either a published name ('oem*.inf') or an original INF name.

    Returns:
        A list of matching published driver names, or an empty list if none found.
    """
    if inf_name.lower().startswith("oem"):
        return [inf_name]

    try:
        result = subprocess.run(
            ["pnputil", "/enum-drivers"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return []

        published_names = []
        current = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                if current:
                    orig = current.get("Original Name", "")
                    pub = current.get("Published Name", "")
                    if orig.lower() == inf_name.lower() and pub:
                        published_names.append(pub)
                    current = {}
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                current[key.strip()] = value.strip()
        # Handle last block (no trailing blank line)
        if current:
            orig = current.get("Original Name", "")
            pub = current.get("Published Name", "")
            if orig.lower() == inf_name.lower() and pub:
                published_names.append(pub)

        return published_names
    except Exception:
        return []


def _verify_deleted(published_names: list[str]) -> list[str]:
    """Return whichever of the given published names are still present in the driver store."""
    try:
        result = subprocess.run(
            ["pnputil", "/enum-drivers"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return published_names  # assume worst-case: all still present

        still_present = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.lower().startswith("published name") and ":" in line:
                _, _, pub = line.partition(":")
                still_present.add(pub.strip().lower())

        return [p for p in published_names if p.lower() in still_present]
    except Exception:
        return published_names  # assume worst-case on any error


def uninstall_isst_driver(inf_name: str) -> dict:
    """Uninstall the ISST driver from the system using pnputil with force.

    Accepts either a published driver name (e.g., 'oem123.inf') or an original
    INF name (e.g., 'IntcBTAu.inf'). When an original name is given, all installed
    driver packages with that original name are resolved and removed. Uses both
    /force and /uninstall flags so that devices bound to the driver are also removed.

    Args:
        inf_name: The published driver name (e.g., 'oem123.inf') or original INF
                  name (e.g., 'IntcBTAu.inf') to uninstall.

    Returns:
        A dict with status and details of the uninstallation result.
    """
    try:
        if not inf_name or not inf_name.lower().endswith(".inf"):
            return {"error": "A valid .inf driver name must be provided (e.g., 'oem123.inf' or 'IntcBTAu.inf')."}

        if not _is_admin():
            return {"status": "failed", "error": _NOT_ADMIN_HINT}

        # Resolve to published oem*.inf name(s)
        published_names = _resolve_published_names(inf_name)

        if not published_names:
            return {
                "status": "failed",
                "message": f"Could not find any installed driver matching '{inf_name}'.",
            }

        results = []
        overall_success = True

        for pub_name in published_names:
            result = subprocess.run(
                ["pnputil", "/delete-driver", pub_name, "/uninstall", "/force"],
                capture_output=True,
                text=True,
                timeout=120,
            )

            entry = {
                "published_name": pub_name,
                "return_code": result.returncode,
                "output": result.stdout.strip(),
                "error_output": result.stderr.strip(),
            }

            if result.returncode == 0:
                entry["status"] = "success"
            else:
                entry["status"] = "failed"
                overall_success = False

            results.append(entry)

        # Post-deletion verification: re-enumerate to confirm removal
        still_present = _verify_deleted(published_names)
        for entry in results:
            entry["verified_deleted"] = entry["published_name"].lower() not in [
                p.lower() for p in still_present
            ]

        verified_failed = [r["published_name"] for r in results if not r["verified_deleted"]]

        if not verified_failed and overall_success:
            return {
                "status": "success",
                "message": f"ISST driver(s) uninstalled and verified deleted ({len(results)} package(s) removed).",
                "results": results,
            }
        elif verified_failed:
            return {
                "status": "failed",
                "message": (
                    f"Uninstall command ran but {len(verified_failed)} package(s) are still present "
                    f"in the driver store: {verified_failed}. "
                    "This usually means the process lacks Administrator privileges."
                ),
                "still_present": verified_failed,
                "results": results,
            }
        else:
            cmd_failed = [r["published_name"] for r in results if r["status"] == "failed"]
            return {
                "status": "failed",
                "message": f"Some driver packages could not be uninstalled: {cmd_failed}",
                "results": results,
            }
    except subprocess.TimeoutExpired:
        return {"error": "Driver uninstallation timed out after 120 seconds."}
    except Exception as e:
        return {"error": str(e)}


# Exact original INF names that belong to the Intel SST driver package (matching
# the .inf files shipped in the driver folders). Matching is case-insensitive.
# An explicit allowlist is used (instead of a broad 'intc*' prefix) so unrelated
# Intel INFs that happen to start with 'intc' are NOT removed.
_ISST_INF_MATCHERS = (
    "intcaudiobus.inf",
    "intcbtau.inf",
    "intcbtle.inf",
    "intcdmic.inf",
    "intcoed.inf",
    "intcsdw.inf",
    "intcsdwbus.inf",
    "intcsst.inf",
    "intcstreaming.inf",
    "intcusb.inf",
    "detectionverificationdrv.inf",
    "lt6911au.inf",
    "intelmvaextension.inf",
)

# INFs that must NEVER be removed even if they resemble ISST names. These are not
# part of the ISST install package (e.g. OEM/platform-specific variants).
_ISST_INF_EXCLUDE = (
    "intc_dmicext_dell_igo.inf",
    "intcoed_oemlibpath_cirrus.inf",
    "intcpmt.inf",
)


def _matches_isst(original_name: str) -> bool:
    """True if an original INF name belongs to the Intel SST driver package."""
    name = (original_name or "").strip().lower()
    if not name or name in _ISST_INF_EXCLUDE:
        return False
    return name in _ISST_INF_MATCHERS


def _enum_isst_published_names() -> list[dict]:
    """Enumerate installed ISST driver packages as [{published_name, original_name}]."""
    result = subprocess.run(
        ["pnputil", "/enum-drivers"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return []

    matched = []
    current = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            if current:
                if _matches_isst(current.get("Original Name", "")) and current.get("Published Name"):
                    matched.append({
                        "published_name": current.get("Published Name", ""),
                        "original_name": current.get("Original Name", ""),
                    })
                current = {}
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
    if current:
        if _matches_isst(current.get("Original Name", "")) and current.get("Published Name"):
            matched.append({
                "published_name": current.get("Published Name", ""),
                "original_name": current.get("Original Name", ""),
            })
    return matched


def uninstall_all_isst_drivers() -> dict:
    """Uninstall EVERY installed ISST (Intel Smart Sound Technology) driver package.

    Enumerates all drivers in the Windows driver store, selects those belonging to
    the Intel SST package (INF names starting with 'intc' plus the companion
    package INFs: DetectionVerificationDrv, LT6911Au, IntelMvaExtension) and removes
    each with pnputil using /uninstall and /force so bound devices are also removed.
    Deletion is then verified by re-enumerating the driver store.

    Returns:
        A dict with an overall status plus per-package uninstall results.
    """
    try:
        if not _is_admin():
            return {"status": "failed", "error": _NOT_ADMIN_HINT}

        matched = _enum_isst_published_names()

        if not matched:
            return {
                "status": "success",
                "removed": 0,
                "message": "No ISST driver packages are currently installed.",
                "results": [],
            }

        published_names = [m["published_name"] for m in matched]
        results = []
        overall_success = True

        for m in matched:
            pub_name = m["published_name"]
            result = subprocess.run(
                ["pnputil", "/delete-driver", pub_name, "/uninstall", "/force"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            entry = {
                "published_name": pub_name,
                "original_name": m["original_name"],
                "return_code": result.returncode,
                "output": result.stdout.strip(),
                "error_output": result.stderr.strip(),
                "status": "success" if result.returncode == 0 else "failed",
            }
            if result.returncode != 0:
                overall_success = False
            results.append(entry)

        # Post-deletion verification: re-enumerate to confirm removal.
        still_present = _verify_deleted(published_names)
        still_present_lower = [p.lower() for p in still_present]
        for entry in results:
            entry["verified_deleted"] = entry["published_name"].lower() not in still_present_lower

        verified_failed = [r["published_name"] for r in results if not r["verified_deleted"]]
        removed = sum(1 for r in results if r["verified_deleted"])

        if not verified_failed and overall_success:
            return {
                "status": "success",
                "removed": removed,
                "message": f"All ISST driver packages uninstalled and verified deleted ({removed} removed).",
                "results": results,
            }
        elif verified_failed:
            return {
                "status": "failed",
                "removed": removed,
                "message": (
                    f"Uninstall ran but {len(verified_failed)} package(s) are still present in the "
                    f"driver store: {verified_failed}. This usually means the process lacks "
                    "Administrator privileges."
                ),
                "still_present": verified_failed,
                "results": results,
            }
        else:
            cmd_failed = [r["published_name"] for r in results if r["status"] == "failed"]
            return {
                "status": "failed",
                "removed": removed,
                "message": f"Some ISST driver packages could not be uninstalled: {cmd_failed}",
                "results": results,
            }
    except subprocess.TimeoutExpired:
        return {"error": "Driver uninstallation timed out after 120 seconds."}
    except Exception as e:
        return {"error": str(e)}



def get_isst_driver_version() -> dict:
    """Get the currently installed ISST (Intel Smart Sound Technology) driver version.

    Uses pnputil to enumerate installed drivers and filters for Intel SST entries
    by matching on original INF names that begin with 'intc' (e.g. IntcAudioBus,
    IntcBTAu, IntcBtLE, IntcDMic, etc.).

    Returns:
        A dict with status and a list of found ISST driver entries, each containing
        the published name, original name, provider, class, driver version, and signer.
    """
    try:
        result = subprocess.run(
            ["pnputil", "/enum-drivers"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            return {
                "status": "failed",
                "message": "pnputil failed to enumerate drivers.",
                "error_output": result.stderr.strip(),
            }

        # Parse pnputil output into individual driver blocks
        drivers = []
        current = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                if current:
                    drivers.append(current)
                    current = {}
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                current[key.strip()] = value.strip()
        if current:
            drivers.append(current)

        # Filter for ISST drivers: original INF name starts with 'intc'
        isst_drivers = [
            d for d in drivers
            if d.get("Original Name", "").lower().startswith("intc")
        ]

        if not isst_drivers:
            return {
                "status": "success",
                "installed": False,
                "version": None,
                "versions": [],
                "packages_count": 0,
                "message": "No ISST driver found on this system.",
                "drivers": [],
            }

        # Normalized version tokens (date prefix stripped) for easy condition checks.
        versions = sorted(
            {
                v for d in isst_drivers
                if (v := _extract_driver_version(d.get("Driver Version", "")))
            },
            key=_version_key,
        )

        # The canonical ISST (Smart Sound) version is the audio-bus driver's
        # (intcaudiobus.inf); other intc* entries (Bluetooth, etc.) use unrelated
        # version lines and must not stand in for the SST version.
        primary = next(
            (
                d for d in isst_drivers
                if d.get("Original Name", "").lower().startswith("intcaudiobus")
            ),
            None,
        )
        primary_version = (
            _extract_driver_version(primary.get("Driver Version", ""))
            if primary
            else (versions[-1] if versions else None)
        )

        return {
            "status": "success",
            "installed": True,
            "version": primary_version,
            "versions": versions,
            "packages_count": len(isst_drivers),
            "message": f"Found {len(isst_drivers)} ISST driver package(s).",
            "drivers": isst_drivers,
        }

    except subprocess.TimeoutExpired:
        return {"error": "pnputil timed out after 60 seconds."}
    except Exception as e:
        return {"error": str(e)}


ISST_DRIVER_INSTALL_ANTHROPIC_TOOLS = [
    {
        "name": "install_isst_driver",
        "description": (
            "Install an ISST (Intel Smart Sound Technology) driver from a .inf file using pnputil. "
            "Provide the full path to the .inf file. "
            "Prefer the driver under the 'Production' subfolder; fall back to 'QS_Cert' if 'Production' is not available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inf_path": {
                    "type": "string",
                    "description": "Full path to the .inf driver file to install.",
                },
            },
            "required": ["inf_path"],
        },
    },
    {
        "name": "install_all_isst_drivers",
        "description": (
            "Install ALL .inf driver files found under a driver folder using pnputil. "
            "Use this instead of calling install_isst_driver once per file: point it at the "
            "version's driver folder (e.g. the 'Production' or 'QS_Cert' folder, or its 'Drivers' "
            "subfolder) and it discovers and installs every .inf automatically. By default it "
            "searches subfolders too, so both 'Drivers' and 'Extensions' INFs are included. "
            "This is the reliable way to satisfy 'install all .inf files'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "driver_folder": {
                    "type": "string",
                    "description": (
                        "Path to the folder containing the .inf files (e.g. the version's "
                        "'Production' or 'QS_Cert' folder, or its 'Drivers' subfolder)."
                    ),
                },
                "recursive": {
                    "type": "boolean",
                    "description": (
                        "When true (default), also install .inf files in nested subfolders "
                        "such as Extensions/OemExtensionInfs."
                    ),
                },
            },
            "required": ["driver_folder"],
        },
    },
    {
        "name": "uninstall_isst_driver",
        "description": (
            "Uninstall an ISST driver from the system using pnputil with /force and /uninstall flags. "
            "Accepts either a published driver name (e.g., 'oem123.inf') or an original INF name "
            "(e.g., 'IntcBTAu.inf'). When an original name is given, all installed packages with "
            "that name are automatically resolved and removed. Devices bound to the driver are also uninstalled."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "inf_name": {
                    "type": "string",
                    "description": (
                        "The published driver name (e.g., 'oem123.inf') or original INF name "
                        "(e.g., 'IntcBTAu.inf') to uninstall."
                    ),
                },
            },
            "required": ["inf_name"],
        },
    },
    {
        "name": "uninstall_all_isst_drivers",
        "description": (
            "Uninstall ALL installed ISST (Intel Smart Sound Technology) driver packages at once. "
            "Enumerates the Windows driver store, selects every Intel SST package driver (INF names "
            "starting with 'intc' plus the companion INFs DetectionVerificationDrv, LT6911Au and "
            "IntelMvaExtension) and removes each with pnputil /uninstall /force, then verifies deletion. "
            "Use this to fully clear ISST drivers without listing each INF name by hand. Requires "
            "Administrator privileges."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_isst_driver_status",
        "description": "Check the current installation status of the ISST driver on this system.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_isst_driver_version",
        "description": (
            "Get the currently installed ISST (Intel Smart Sound Technology) driver version. "
            "Returns all installed ISST driver packages with their version numbers, published names, "
            "and other metadata. Use this to check what version is currently on the system before "
            "deciding whether an install or upgrade is needed. "
            "Result keys for conditions: 'status' (\"success\"), 'installed' (bool — use "
            "'installed == false' to detect no driver), 'version' (the SST audio-bus "
            "version string like \"20.40.12741.9\", or null when none is installed), "
            "'versions' (list of all normalized versions found), 'packages_count' "
            "(number of ISST driver packages installed), and 'drivers' (per-package "
            "details). To branch on the installed version use "
            "'version == 20.40.12741.9'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

ISST_DRIVER_INSTALL_TOOL_FUNCTIONS = {
    "install_isst_driver": install_isst_driver,
    "install_all_isst_drivers": install_all_isst_drivers,
    "uninstall_isst_driver": uninstall_isst_driver,
    "uninstall_all_isst_drivers": uninstall_all_isst_drivers,
    "get_isst_driver_version": get_isst_driver_version,
}
