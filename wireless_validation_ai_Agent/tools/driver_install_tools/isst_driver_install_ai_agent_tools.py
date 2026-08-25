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

# pnputil success codes that mean the operation completed but Windows needs a
# reboot to finish. 3010 = ERROR_SUCCESS_REBOOT_REQUIRED, 1641 = ERROR_SUCCESS_REBOOT_INITIATED.
_REBOOT_REQUIRED_CODES = (3010, 1641)


def _uninstall_needs_reboot(return_code: int, output: str) -> bool:
    """True if a pnputil delete/uninstall result indicates a reboot-pending success."""
    text = (output or "").lower()
    return return_code in _REBOOT_REQUIRED_CODES or "reboot" in text or "restart" in text


def _read_inf_text(inf_path: str) -> str:
    """Read an INF as text, honoring its BOM (INFs are often UTF-16 LE)."""
    with open(inf_path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="ignore")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="ignore")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="ignore")


def _is_extension_inf(inf_path: str) -> bool:
    """Return True if the INF declares ``Class = Extension``.

    Extension-class INFs (e.g. IntelMvaExtension.inf) cannot be installed on their
    own with ``pnputil /add-driver /install``: they only attach to an already
    matched primary device, so a standalone install often reports a non-zero code.
    Detecting them lets the bulk installer treat such failures as non-fatal.
    """
    try:
        for line in _read_inf_text(inf_path).splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("class") and "=" in stripped:
                key, value = stripped.split("=", 1)
                if key.strip().lower() == "class" and value.strip().strip('"').lower() == "extension":
                    return True
    except Exception:
        pass
    return False


def _install_inf(inf_path: str) -> dict:
    """Install a single .inf via pnputil (internal helper for install_all_isst_drivers)."""
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

        # 3010 = ERROR_SUCCESS_REBOOT_REQUIRED, 1641 = ERROR_SUCCESS_REBOOT_INITIATED.
        # These are SUCCESS codes: the driver installed but Windows needs a reboot to
        # finish binding it (common for the primary IntcAudioBus.inf on a live device).
        reboot_required = (
            result.returncode in (3010, 1641)
            or "reboot" in output.lower()
            or "restart" in output.lower()
        )

        if result.returncode == 0:
            return {
                "status": "success",
                "message": "ISST driver installed successfully.",
                "output": output,
            }
        elif reboot_required:
            return {
                "status": "success",
                "message": "ISST driver installed; a reboot is required to complete installation.",
                "reboot_required": True,
                "return_code": result.returncode,
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


def install_all_isst_drivers(
    driver_folder: str,
    recursive: bool = True,
    ignore_infs: list | None = None,
    ignore_extension_inf_failures: bool = True,
) -> dict:
    """Install EVERY ``.inf`` file found under a driver folder using pnputil.

    This is the reliable way to satisfy "install ALL .inf files" — instead of
    listing each INF name by hand (which is error-prone and easy to leave
    incomplete), point this at a version's driver folder and it discovers and
    installs every ``.inf`` automatically. By default it searches subfolders too,
    so both ``Drivers`` and ``Extensions`` INFs under the folder are included.

    Some INFs cannot be installed standalone and will always report a failure —
    most notably ``Class = Extension`` INFs (e.g. ``IntelMvaExtension.inf``), which
    only attach to an already-matched primary device. Such failures are expected
    and non-fatal, so by default they are recorded as ``ignored`` and do NOT fail
    the overall install. Use ``ignore_infs`` to whitelist additional INF names.

    Args:
        driver_folder: Path to the folder containing the ``.inf`` files (e.g. the
            version's ``Production`` or ``QS_Cert`` folder, or its ``Drivers``
            subfolder).
        recursive: When True (default), also install ``.inf`` files in nested
            subfolders (e.g. ``Extensions/OemExtensionInfs/...``).
        ignore_infs: Optional list of INF file names (e.g. ``"IntelMvaExtension.inf"``)
            whose install failures should be treated as non-fatal (``ignored``)
            instead of failing the overall result. Matching is case-insensitive.
        ignore_extension_inf_failures: When True (default), any INF that declares
            ``Class = Extension`` and fails to install is treated as ``ignored``
            rather than ``failed``.

    Returns:
        A dict with an overall status plus per-file installation results. The
        overall status is ``success`` when no non-ignored INF failed.
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

        ignore_set = {
            name.strip().lower()
            for name in (ignore_infs or [])
            if isinstance(name, str) and name.strip()
        }

        results = []
        installed = 0
        ignored = 0
        failed = 0
        reboot_required = False
        for inf_path in inf_paths:
            inf_name = os.path.basename(inf_path)
            is_extension = _is_extension_inf(inf_path)
            res = _install_inf(inf_path)
            status = res.get("status", "error" if res.get("error") else "unknown")
            inf_reboot = bool(res.get("reboot_required"))
            if inf_reboot:
                reboot_required = True

            if status == "success":
                installed += 1
                outcome = "success"
            elif inf_name.lower() in ignore_set or (ignore_extension_inf_failures and is_extension):
                ignored += 1
                outcome = "ignored"
            else:
                failed += 1
                outcome = "failed"

            results.append({
                "inf_path": inf_path,
                "inf_name": inf_name,
                "status": status,
                "outcome": outcome,
                "is_extension": is_extension,
                "reboot_required": inf_reboot,
                "return_code": res.get("return_code"),
                "message": res.get("message") or res.get("error", ""),
                "output": res.get("output", ""),
                "error_output": res.get("error_output", ""),
            })

        total = len(inf_paths)
        overall = "success" if failed == 0 else "failed"
        summary = f"Installed {installed}/{total} .inf file(s)"
        if ignored:
            summary += f", ignored {ignored} non-fatal failure(s)"
        if reboot_required:
            summary += ", reboot required to complete installation"
        summary += f" from '{driver_folder}'."
        return {
            "status": overall,
            "message": summary,
            "driver_folder": driver_folder,
            "total": total,
            "installed": installed,
            "ignored": ignored,
            "failed": failed,
            "reboot_required": reboot_required,
            "ignored_infs": [r["inf_name"] for r in results if r["outcome"] == "ignored"],
            "failed_infs": [r["inf_name"] for r in results if r["outcome"] == "failed"],
            "reboot_required_infs": [r["inf_name"] for r in results if r["reboot_required"]],
            "results": results,
        }
    except Exception as e:
        return {"error": str(e)}


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

    A pnputil reboot-required code (3010 = ERROR_SUCCESS_REBOOT_REQUIRED, 1641 =
    ERROR_SUCCESS_REBOOT_INITIATED) is treated as SUCCESS: the uninstall completed
    but Windows needs a reboot to finish. Such packages may still appear in the
    driver store until the reboot, so they are reported as 'reboot_pending' and
    counted as removed rather than failed. When any package needs a reboot the
    overall result sets 'reboot_required': true; callers should reboot before
    reinstalling to avoid a half-removed driver state.

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
        reboot_required = False

        for m in matched:
            pub_name = m["published_name"]
            result = subprocess.run(
                ["pnputil", "/delete-driver", pub_name, "/uninstall", "/force"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            needs_reboot = _uninstall_needs_reboot(result.returncode, result.stdout)
            if needs_reboot:
                reboot_required = True
            entry = {
                "published_name": pub_name,
                "original_name": m["original_name"],
                "return_code": result.returncode,
                "reboot_required": needs_reboot,
                "output": result.stdout.strip(),
                "error_output": result.stderr.strip(),
                # A reboot-required code (3010/1641) is a success that needs a reboot.
                "status": "success" if (result.returncode == 0 or needs_reboot) else "failed",
            }
            if result.returncode != 0 and not needs_reboot:
                overall_success = False
            results.append(entry)

        # Post-deletion verification: re-enumerate to confirm removal.
        still_present = _verify_deleted(published_names)
        still_present_lower = [p.lower() for p in still_present]
        for entry in results:
            present = entry["published_name"].lower() in still_present_lower
            entry["verified_deleted"] = not present
            # A reboot-pending package may still appear in the store until reboot; not a failure.
            entry["reboot_pending"] = bool(present and entry["reboot_required"])

        # Only packages still present AND not reboot-pending are genuine failures.
        verified_failed = [
            r["published_name"] for r in results
            if not r["verified_deleted"] and not r["reboot_pending"]
        ]
        # Count reboot-pending packages as removed: the uninstall succeeded, only the
        # reboot is outstanding.
        removed = sum(1 for r in results if r["verified_deleted"] or r["reboot_pending"])

        if not verified_failed and overall_success:
            if reboot_required:
                message = (
                    f"All ISST driver packages uninstalled ({removed} package(s)); a reboot is "
                    "required to complete removal. Reboot before reinstalling."
                )
            else:
                message = f"All ISST driver packages uninstalled and verified deleted ({removed} removed)."
            return {
                "status": "success",
                "removed": removed,
                "reboot_required": reboot_required,
                "message": message,
                "results": results,
            }
        elif verified_failed:
            return {
                "status": "failed",
                "removed": removed,
                "reboot_required": reboot_required,
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
                "reboot_required": reboot_required,
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
        "name": "install_all_isst_drivers",
        "description": (
            "Install ALL .inf driver files found under a driver folder using pnputil. "
            "Point it at the version's driver folder (e.g. the 'Production' or 'QS_Cert' folder, "
            "or its 'Drivers' subfolder) and it discovers and installs every .inf automatically. "
            "By default it searches subfolders too, so both 'Drivers' and 'Extensions' INFs are "
            "included. This is the reliable way to satisfy 'install all .inf files'. "
            "A pnputil reboot-required code (3010/1641) is treated as SUCCESS: the driver is "
            "installed but Windows needs a reboot to finish binding it (common for the primary "
            "IntcAudioBus.inf). In that case the result sets 'reboot_required': true and lists "
            "'reboot_required_infs'; this is NOT a failure. Only 'failed_infs' indicate real "
            "install failures."
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
                "ignore_infs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of INF file names (e.g. 'IntelMvaExtension.inf') whose "
                        "install failures should be treated as non-fatal (recorded as 'ignored') "
                        "instead of failing the overall result. Matching is case-insensitive."
                    ),
                },
                "ignore_extension_inf_failures": {
                    "type": "boolean",
                    "description": (
                        "When true (default), any INF declaring 'Class = Extension' (such as "
                        "IntelMvaExtension.inf) that fails to install is treated as 'ignored' "
                        "rather than 'failed'. Extension INFs cannot be installed standalone."
                    ),
                },
            },
            "required": ["driver_folder"],
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
            "Administrator privileges. "
            "A pnputil reboot-required code (3010/1641) is treated as SUCCESS: the uninstall completed "
            "but Windows needs a reboot to finish. In that case the result sets 'reboot_required': true "
            "and the affected package(s) are marked 'reboot_pending' (still in the store until reboot) "
            "and counted as removed, NOT failed. When 'reboot_required' is true you MUST reboot before "
            "reinstalling the drivers, otherwise the reinstall runs against a half-removed driver state."
        ),
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
    "install_all_isst_drivers": install_all_isst_drivers,
    "uninstall_all_isst_drivers": uninstall_all_isst_drivers,
    "get_isst_driver_version": get_isst_driver_version,
}
