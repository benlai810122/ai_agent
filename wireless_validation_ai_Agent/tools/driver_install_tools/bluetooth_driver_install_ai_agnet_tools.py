import os
import re
import subprocess


def _extract_driver_version(text: str):
    """Pull the dotted version token (e.g. 24.40.0.3) out of a pnputil value."""
    m = re.search(r"\d+\.\d+\.\d+(?:\.\d+)?", text or "")
    return m.group(0) if m else None


def _version_key(version: str):
    """Numeric sort key so 24.40.0.3 ranks above 23.160.0.9 correctly (not lexicographically)."""
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


# SPAPI_E_NO_SUCH_DEVINST: /uninstall failed only because the driver's device instance
# is no longer in the hardware tree (a stale/orphaned store entry with nothing bound).
_NO_DEVINST_CODE = 0xE000020B


def _device_instance_missing(return_code: int, output: str, err_output: str = "") -> bool:
    """True if pnputil failed only because the device instance is gone from the hardware tree.

    This is a stale/orphaned driver-store entry with no bound device, so /uninstall has
    nothing to remove. Treated as a non-fatal warning rather than a real failure.
    """
    text = f"{output or ''} {err_output or ''}".lower()
    return return_code == _NO_DEVINST_CODE or "does not exist in the hardware tree" in text


def _matches_bluetooth(original_name: str) -> bool:
    """True if an original INF name belongs to the Intel Bluetooth (IBT) driver package."""
    name = (original_name or "").strip().lower()
    return name.startswith("ibt")


# The Bluetooth transport INF to install is decided by the wireless module, with
# BE201 as the boundary: modules older than BE201 (all AX modules and BE200) use USB
# (ibtusb.inf); modules newer than BE201 (BE202 and up) use PCIe (ibtpci.inf). BE201
# itself supports BOTH transports, so its transport is detected from the currently
# installed Bluetooth driver. The UART transport (ibtuart.inf) is out of scope.
_TRANSPORT_INF = {
    "pci": "ibtpci.inf",
    "usb": "ibtusb.inf",
}

# install_bluetooth_driver selects the INF directly by type.
_INF_TYPES = {
    "ibtpci": "ibtpci.inf",
    "ibtusb": "ibtusb.inf",
}

# When ibtusb is installed, the ibtusb.inf lives under a per-module "peak" profile
# subfolder (e.g. ...\ibtusb\TYP\ibtusb.inf). Maps the wireless module to its peak.
_USB_PEAK_BY_MODULE = {
    "AX201": "HRP",
    "AX210": "THP",
    "AX211": "TYP",
    "BE200": "GAP",
    "BE201": "FMP",
    "BE202": "GFP",
}


def _module_bt_transport(module: str):
    """Return 'pci', 'usb', or 'detect' for a wireless module token (e.g. 'AX211', 'BE201').

    Boundary is BE201: all AX modules and BE200 (older than BE201) use USB; BE202 and
    newer use PCIe. BE201 supports both transports and returns 'detect' so the caller
    resolves it from the currently installed Bluetooth driver. Returns None when the
    module cannot be classified.
    """
    m = re.search(r"(AX|BE)\s*(\d{3,4})", (module or "").upper())
    if not m:
        return None
    prefix, number = m.group(1), int(m.group(2))
    if prefix == "AX":
        return "usb"
    if number < 201:
        return "usb"
    if number == 201:
        return "detect"
    return "pci"


def _present_bt_device_inf():
    """Return the published INF (e.g. 'oem40.inf') bound to the present Intel BT radio, or None."""
    try:
        ps = (
            "Get-PnpDevice -Class Bluetooth -PresentOnly | "
            "Where-Object { $_.FriendlyName -match 'Intel' } | "
            "ForEach-Object { ($_ | Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_DriverInfPath').Data }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.lower().endswith(".inf"):
                return line
    except Exception:
        return None
    return None


def _detect_installed_bt_transport():
    """Best-effort transport ('pci'/'usb') of the currently installed Intel BT driver.

    Used to disambiguate BE201, which supports both transports. Maps the INF bound to
    the present Bluetooth radio to its original IBT name; if that is unavailable, falls
    back to whichever single IBT transport package is present in the driver store.
    Returns None when it cannot be determined.
    """
    packages = _enum_bluetooth_published_names()
    by_published = {
        p["published_name"].lower(): p["original_name"].lower() for p in packages
    }

    bound = _present_bt_device_inf()
    if bound:
        original = by_published.get(bound.lower(), "")
        if original.startswith("ibtpci"):
            return "pci"
        if original.startswith("ibtusb"):
            return "usb"

    originals = set(by_published.values())
    has_pci = any(o.startswith("ibtpci") for o in originals)
    has_usb = any(o.startswith("ibtusb") for o in originals)
    if has_pci and not has_usb:
        return "pci"
    if has_usb and not has_pci:
        return "usb"
    return None


def _bt_hardware_ids() -> list[str]:
    """Return the hardware IDs of the present Intel Bluetooth radio (best-effort).

    Also scans devices outside the Bluetooth class so the transport can still be read
    when the functional driver is uninstalled and the radio shows up as an unknown /
    other device.
    """
    ids = []
    queries = [
        # Present Intel devices in the Bluetooth class.
        "Get-PnpDevice -Class Bluetooth -PresentOnly | "
        "Where-Object { $_.FriendlyName -match 'Intel' } | "
        "ForEach-Object { ($_ | Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_HardwareIds').Data }",
        # Fallback: any present device that looks like the Intel BT radio, including
        # unknown/other devices when the functional driver is not yet installed.
        "Get-PnpDevice -PresentOnly | "
        "Where-Object { $_.InstanceId -match 'VID_8087' -or $_.FriendlyName -match 'Bluetooth' } | "
        "ForEach-Object { ($_ | Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_HardwareIds').Data }",
    ]
    for ps in queries:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=30,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    ids.append(line)
        except Exception:
            continue
    return ids


def _detect_bt_transport_by_hw_id():
    """Return 'usb', 'pci', or None from the present Intel BT radio's hardware ID bus prefix.

    A USB Bluetooth radio enumerates as ``USB\\VID_8087...`` (-> ibtusb.inf); a PCIe
    radio enumerates as ``PCI\\VEN_8086...`` (-> ibtpci.inf).
    """
    upper = [i.upper() for i in _bt_hardware_ids()]
    if any(i.startswith("USB\\") and "VID_8087" in i for i in upper):
        return "usb"
    if any(i.startswith("PCI\\") and "VEN_8086" in i for i in upper):
        return "pci"
    return None


def _detect_wireless_module() -> dict:
    """Detect the Intel wireless (Wi-Fi) module and the BT transport it uses.

    Returns a dict with the adapter friendly name, the parsed module token (e.g.
    'AX211', 'BE200') and the Bluetooth transport ('pci' or 'usb'). Fields are None
    when nothing could be detected.
    """
    info = {"adapter_name": None, "module": None, "transport": None}
    try:
        ps = (
            "Get-PnpDevice -Class Net -PresentOnly | "
            "Where-Object { $_.FriendlyName -match 'Wi-Fi|Wireless|WiFi' } | "
            "Select-Object -ExpandProperty FriendlyName"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
        )
        names = [n.strip() for n in result.stdout.splitlines() if n.strip()]
        # Prefer an Intel adapter name; fall back to the first Wi-Fi adapter found.
        adapter = next((n for n in names if "intel" in n.lower()), names[0] if names else None)
        info["adapter_name"] = adapter
        if adapter:
            token = re.search(r"\b(AX|BE)\s*(\d{3,4})\b", adapter, re.IGNORECASE)
            if token:
                info["module"] = f"{token.group(1).upper()}{token.group(2)}"
                transport = _module_bt_transport(info["module"])
                # BE201 supports both transports: resolve from the installed BT driver
                # (fallback to USB when none is installed).
                if transport == "detect":
                    detected = _detect_installed_bt_transport()
                    info["transport"] = detected or "usb"
                    info["transport_source"] = (
                        "installed_driver" if detected else "be201_default_usb"
                    )
                else:
                    info["transport"] = transport
                    info["transport_source"] = "module"
    except Exception as e:
        info["error"] = str(e)
    return info


def get_wireless_module() -> dict:
    """Detect the installed Intel wireless module and which Bluetooth transport it uses.

    The Bluetooth driver transport to install depends on the wireless module, with
    BE201 as the boundary: modules older than BE201 (all AX modules and BE200) use USB
    Bluetooth (ibtusb.inf); modules newer than BE201 (BE202 and up) use PCIe Bluetooth
    (ibtpci.inf). BE201 supports both transports, so it is resolved from the currently
    installed Bluetooth driver (defaulting to USB when none is installed).

    Returns:
        A dict with 'status', the detected 'module' token (e.g. 'AX211'), the
        'transport' ('pci' or 'usb'), how it was decided ('transport_source'), the
        'recommended_inf' file name, and the raw 'adapter_name'.
    """
    info = _detect_wireless_module()
    if not info.get("module") or not info.get("transport"):
        return {
            "status": "not_found",
            "message": (
                "Could not detect a supported Intel wireless module (AX/BE) to decide the "
                "Bluetooth transport."
            ),
            "adapter_name": info.get("adapter_name"),
            "module": info.get("module"),
            "transport": info.get("transport"),
        }
    return {
        "status": "success",
        "module": info["module"],
        "transport": info["transport"],
        "transport_source": info.get("transport_source"),
        "recommended_inf": _TRANSPORT_INF[info["transport"]],
        "adapter_name": info.get("adapter_name"),
        "message": (
            f"Wireless module '{info['module']}' uses the {info['transport'].upper()} "
            f"Bluetooth transport -> install {_TRANSPORT_INF[info['transport']]}."
        ),
    }


def _no_matching_device(output: str, err_output: str) -> bool:
    """True if pnputil reports the INF was added but no present device matched it.

    The Bluetooth package contains many per-adapter ``ibtusb.inf`` variants; on a
    given machine only the one matching the installed adapter binds. For every other
    variant pnputil adds the package to the store but installs it on no device. That
    is expected and non-fatal, not a real install failure.
    """
    text = f"{output or ''} {err_output or ''}".lower()
    return (
        "no devices" in text
        or "no matching" in text
        or "not installed on any device" in text
        or "no compatible" in text
    )


def _install_inf(inf_path: str) -> dict:
    """Install a single .inf via pnputil (internal helper for install_bluetooth_driver)."""
    try:
        if not inf_path or not inf_path.lower().endswith(".inf"):
            return {"error": "A valid .inf file path must be provided."}

        if not os.path.isfile(inf_path):
            return {"error": f"INF file not found: {inf_path}"}

        if not _is_admin():
            return {"status": "failed", "error": _NOT_ADMIN_HINT}

        result = subprocess.run(
            ["pnputil", "/add-driver", inf_path, "/install"],
            capture_output=True,
            text=True,
            timeout=120,
        )

        output = result.stdout.strip()
        err_output = result.stderr.strip()

        # 259 (ERROR_NO_MORE_ITEMS) means the package is already present / up-to-date.
        already_present = (
            result.returncode == 259
            or "already exists" in output.lower()
            or "up-to-date" in output.lower()
        )

        # 3010 / 1641 are SUCCESS codes: installed but Windows needs a reboot to finish.
        reboot_required = (
            result.returncode in (3010, 1641)
            or "reboot" in output.lower()
            or "restart" in output.lower()
        )

        no_device = _no_matching_device(output, err_output)

        if result.returncode == 0:
            return {
                "status": "success",
                "message": "Bluetooth driver installed successfully.",
                "output": output,
            }
        elif reboot_required:
            return {
                "status": "success",
                "message": "Bluetooth driver installed; a reboot is required to complete installation.",
                "reboot_required": True,
                "return_code": result.returncode,
                "output": output,
            }
        elif already_present:
            return {
                "status": "success",
                "message": "Bluetooth driver already present / up-to-date on the system.",
                "return_code": result.returncode,
                "output": output,
            }
        elif no_device:
            return {
                "status": "no_matching_device",
                "message": (
                    "INF added to the driver store but no present device matched it "
                    "(expected for non-matching adapter variants)."
                ),
                "return_code": result.returncode,
                "output": output,
                "error_output": err_output,
            }
        else:
            return {
                "status": "failed",
                "message": "Bluetooth driver installation failed.",
                "return_code": result.returncode,
                "output": output,
                "error_output": err_output,
            }
    except subprocess.TimeoutExpired:
        return {"error": "Driver installation timed out after 120 seconds."}
    except Exception as e:
        return {"error": str(e)}


def install_bluetooth_driver(
    driver_folder: str,
    recursive: bool = True,
    ignore_infs: list | None = None,
    ignore_no_matching_device: bool = True,
) -> dict:
    """Install the correct Bluetooth (Intel IBT) driver INF from a package folder using pnputil.

    Point this at a version's driver folder (e.g. the extracted package folder or its
    ``INF_INSTALL`` subtree) and it selects and installs a single transport INF instead
    of installing every INF blindly.

    The INF is chosen from the present Bluetooth radio's hardware ID bus prefix: a USB
    radio (``USB\\VID_8087...``) installs ``ibtusb.inf`` and a PCIe radio
    (``PCI\\VEN_8086...``) installs ``ibtpci.inf``. If no present Intel Bluetooth radio
    is found on either bus the call returns ``not_found``.

    When ``ibtusb`` is installed the package ships one ``ibtusb.inf`` per "peak" profile
    subfolder (FMP, GAP, GFP, HRP, JFP, MTP, THP, TYP, ...). The peak is chosen from the
    wireless module: AX201->HRP, AX210->THP, AX211->TYP, BE200->GAP, BE201->FMP, BE202->GFP.
    If the module has no peak mapping (or the peak folder is missing) the call returns
    ``not_found``.

    Args:
        driver_folder: Path to the folder containing the ``.inf`` files.
        recursive: When True (default), also search nested subfolders for ``.inf`` files.
        ignore_infs: Optional list of INF file names whose install failures should be treated
            as non-fatal (``ignored``). Matching is case-insensitive.
        ignore_no_matching_device: When True (default), an INF that is added to the store but
            matches no present device is treated as ``ignored`` rather than ``failed``.

    Returns:
        A dict with an overall status plus per-file installation results. The overall status
        is ``success`` when at least one INF installed and no non-ignored INF failed.
    """
    try:
        if not driver_folder or not isinstance(driver_folder, str):
            return {"error": "A valid driver_folder path must be provided."}

        if not os.path.isdir(driver_folder):
            return {"error": f"Driver folder not found: {driver_folder}"}

        if not _is_admin():
            return {"status": "failed", "error": _NOT_ADMIN_HINT}

        module_info = _detect_wireless_module()
        module = (module_info or {}).get("module")

        # Decide the INF from the present Bluetooth radio's hardware ID bus prefix:
        # USB (USB\VID_8087) -> ibtusb.inf, PCIe (PCI\VEN_8086) -> ibtpci.inf.
        transport = _detect_bt_transport_by_hw_id()
        if transport not in ("usb", "pci"):
            return {
                "status": "not_found",
                "message": (
                    "Could not determine the Bluetooth transport from the hardware ID. No present "
                    "Intel Bluetooth radio on a USB (USB\\VID_8087) or PCIe (PCI\\VEN_8086) bus "
                    "was found."
                ),
                "adapter_name": (module_info or {}).get("adapter_name"),
            }
        chosen = "ibtusb" if transport == "usb" else "ibtpci"
        inf_source = "hardware_id"

        # For USB, pick the module's peak profile subfolder.
        peak = None
        if chosen == "ibtusb":
            if not module:
                return {
                    "status": "failed",
                    "message": (
                        "USB Bluetooth needs the wireless module to pick the peak profile folder, "
                        "but the module could not be detected."
                    ),
                    "adapter_name": (module_info or {}).get("adapter_name"),
                }
            peak = _USB_PEAK_BY_MODULE.get(module.upper())
            if not peak:
                return {
                    "status": "not_found",
                    "message": (
                        f"No USB peak profile mapping for module '{module}'. Known modules: "
                        f"{sorted(_USB_PEAK_BY_MODULE)}."
                    ),
                    "module": module,
                    "inf_type": chosen,
                    "adapter_name": (module_info or {}).get("adapter_name"),
                }

        target_inf = _INF_TYPES[chosen]

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

        # Keep the chosen INF, and for USB only the mapped peak profile subfolder.
        selected = []
        for p in inf_paths:
            if os.path.basename(p).lower() != target_inf:
                continue
            if peak is not None and os.path.basename(os.path.dirname(p)).lower() != peak.lower():
                continue
            selected.append(p)
        inf_paths = sorted(selected)

        if not inf_paths:
            where = f" under peak '{peak}'" if peak else ""
            return {
                "status": "not_found",
                "message": (
                    f"No '{target_inf}' file found{where} under '{driver_folder}'."
                ),
                "driver_folder": driver_folder,
                "inf_type": chosen,
                "target_inf": target_inf,
                "peak": peak,
                "module": module,
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
            res = _install_inf(inf_path)
            status = res.get("status", "error" if res.get("error") else "unknown")
            inf_reboot = bool(res.get("reboot_required"))
            if inf_reboot:
                reboot_required = True

            if status == "success":
                installed += 1
                outcome = "success"
            elif status == "no_matching_device" and ignore_no_matching_device:
                ignored += 1
                outcome = "ignored"
            elif inf_name.lower() in ignore_set:
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
                "reboot_required": inf_reboot,
                "return_code": res.get("return_code"),
                "message": res.get("message") or res.get("error", ""),
                "output": res.get("output", ""),
                "error_output": res.get("error_output", ""),
            })

        total = len(inf_paths)
        overall = "success" if (failed == 0 and installed > 0) else "failed"
        peak_label = f" [peak {peak}]" if peak else ""
        summary = f"Installed {installed}/{total} '{target_inf}'{peak_label} file(s)"
        if ignored:
            summary += f", ignored {ignored} non-matching/non-fatal result(s)"
        if reboot_required:
            summary += ", reboot required to complete installation"
        summary += f" from '{driver_folder}'."
        return {
            "status": overall,
            "message": summary,
            "driver_folder": driver_folder,
            "inf_type": chosen,
            "inf_source": inf_source,
            "transport": transport,
            "target_inf": target_inf,
            "peak": peak,
            "module": module,
            "adapter_name": (module_info or {}).get("adapter_name"),
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


def _enum_bluetooth_published_names() -> list[dict]:
    """Enumerate installed Bluetooth (IBT) driver packages as [{published_name, original_name}]."""
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
                if _matches_bluetooth(current.get("Original Name", "")) and current.get("Published Name"):
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
        if _matches_bluetooth(current.get("Original Name", "")) and current.get("Published Name"):
            matched.append({
                "published_name": current.get("Published Name", ""),
                "original_name": current.get("Original Name", ""),
            })
    return matched


def uninstall_bluetooth_driver() -> dict:
    """Uninstall the installed Bluetooth (Intel IBT) driver package(s).

    Enumerates all drivers in the Windows driver store, selects those belonging to
    the Intel Bluetooth package (original INF names starting with 'ibt', e.g.
    ibtusb.inf, ibtpci.inf, ibtuart.inf) and removes each with pnputil using
    /uninstall and /force so bound devices are also removed. Deletion is then
    verified by re-enumerating the driver store.

    A pnputil reboot-required code (3010 = ERROR_SUCCESS_REBOOT_REQUIRED, 1641 =
    ERROR_SUCCESS_REBOOT_INITIATED) is treated as SUCCESS: the uninstall completed
    but Windows needs a reboot to finish. Such packages may still appear in the
    driver store until the reboot, so they are reported as 'reboot_pending' and
    counted as removed rather than failed. When any package needs a reboot the
    overall result sets 'reboot_required': true; callers should reboot before
    reinstalling to avoid a half-removed driver state.

    A package whose device instance is no longer in the hardware tree
    (SPAPI_E_NO_SUCH_DEVINST, 0xE000020B) is a stale/orphaned store entry with
    nothing bound to uninstall; it is treated as a non-fatal WARNING (listed in
    'warnings' with per-package 'device_instance_missing': true) and does not fail
    the overall result.

    Returns:
        A dict with an overall status plus per-package uninstall results.
    """
    try:
        if not _is_admin():
            return {"status": "failed", "error": _NOT_ADMIN_HINT}

        matched = _enum_bluetooth_published_names()

        if not matched:
            return {
                "status": "success",
                "removed": 0,
                "message": "No Bluetooth driver packages are currently installed.",
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
            devinst_missing = _device_instance_missing(
                result.returncode, result.stdout, result.stderr
            )
            if needs_reboot:
                reboot_required = True
            if result.returncode == 0 or needs_reboot:
                status = "success"
            elif devinst_missing:
                status = "warning"
            else:
                status = "failed"
            entry = {
                "published_name": pub_name,
                "original_name": m["original_name"],
                "return_code": result.returncode,
                "reboot_required": needs_reboot,
                "device_instance_missing": devinst_missing,
                "output": result.stdout.strip(),
                "error_output": result.stderr.strip(),
                "status": status,
            }
            if status == "failed":
                overall_success = False
            results.append(entry)

        # Post-deletion verification: re-enumerate to confirm removal.
        still_present = _verify_deleted(published_names)
        still_present_lower = [p.lower() for p in still_present]
        for entry in results:
            present = entry["published_name"].lower() in still_present_lower
            entry["verified_deleted"] = not present
            entry["reboot_pending"] = bool(present and entry["reboot_required"])

        # A stale package whose device instance is gone (SPAPI_E_NO_SUCH_DEVINST) is a
        # warning, not a failure, even if it lingers in the driver store.
        verified_failed = [
            r["published_name"] for r in results
            if not r["verified_deleted"] and not r["reboot_pending"]
            and not r["device_instance_missing"]
        ]
        warnings = [
            r["published_name"] for r in results if r["device_instance_missing"]
        ]
        removed = sum(1 for r in results if r["verified_deleted"] or r["reboot_pending"])

        if not verified_failed and overall_success:
            if reboot_required:
                message = (
                    f"All Bluetooth driver packages uninstalled ({removed} package(s)); a reboot is "
                    "required to complete removal. Reboot before reinstalling."
                )
            else:
                message = f"All Bluetooth driver packages uninstalled and verified deleted ({removed} removed)."
            if warnings:
                message += (
                    f" {len(warnings)} package(s) skipped as a warning (device instance not in the "
                    f"hardware tree): {warnings}."
                )
            return {
                "status": "success",
                "removed": removed,
                "reboot_required": reboot_required,
                "warnings": warnings,
                "message": message,
                "results": results,
            }
        elif verified_failed:
            return {
                "status": "failed",
                "removed": removed,
                "reboot_required": reboot_required,
                "warnings": warnings,
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
                "warnings": warnings,
                "message": f"Some Bluetooth driver packages could not be uninstalled: {cmd_failed}",
                "results": results,
            }
    except subprocess.TimeoutExpired:
        return {"error": "Driver uninstallation timed out after 120 seconds."}
    except Exception as e:
        return {"error": str(e)}


def get_bluetooth_driver_version() -> dict:
    """Get the currently installed Bluetooth (Intel IBT) driver version.

    Uses pnputil to enumerate installed drivers and filters for Intel Bluetooth
    entries by matching on original INF names that begin with 'ibt' (e.g. ibtusb,
    ibtpci, ibtuart).

    Returns:
        A dict with status and a list of found Bluetooth driver entries, each
        containing the published name, original name, provider, class, driver
        version, and signer.
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

        # Filter for Bluetooth drivers: original INF name starts with 'ibt'
        bt_drivers = [
            d for d in drivers
            if _matches_bluetooth(d.get("Original Name", ""))
        ]

        if not bt_drivers:
            return {
                "status": "success",
                "installed": False,
                "version": None,
                "versions": [],
                "packages_count": 0,
                "message": "No Bluetooth driver found on this system.",
                "drivers": [],
            }

        versions = sorted(
            {
                v for d in bt_drivers
                if (v := _extract_driver_version(d.get("Driver Version", "")))
            },
            key=_version_key,
        )

        # All IBT transport INFs in a package share the same version; prefer the
        # bound USB transport (ibtusb) if present, otherwise fall back to the
        # highest version found.
        primary = next(
            (
                d for d in bt_drivers
                if d.get("Original Name", "").lower().startswith("ibtusb")
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
            "packages_count": len(bt_drivers),
            "message": f"Found {len(bt_drivers)} Bluetooth driver package(s).",
            "drivers": bt_drivers,
        }

    except subprocess.TimeoutExpired:
        return {"error": "pnputil timed out after 60 seconds."}
    except Exception as e:
        return {"error": str(e)}


BLUETOOTH_DRIVER_INSTALL_ANTHROPIC_TOOLS = [
    {
        "name": "install_bluetooth_driver",
        "description": (
            "Install one Intel Bluetooth (IBT) INF from a driver package folder via pnputil. "
            "The INF is chosen from the present Bluetooth radio's hardware ID bus prefix: a USB "
            "radio (USB\\VID_8087) installs 'ibtusb.inf' and a PCIe radio (PCI\\VEN_8086) installs "
            "'ibtpci.inf'; if no present Intel BT radio is found on either bus it returns 'not_found'. "
            "For 'ibtusb' the per-module peak profile folder is used (AX201->HRP, AX210->THP, "
            "AX211->TYP, BE200->GAP, BE201->FMP, BE202->GFP); an unmapped module returns 'not_found'. "
            "A reboot-required code (3010/1641) counts as success; only 'failed_infs' are real failures."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "driver_folder": {
                    "type": "string",
                    "description": (
                        "Path to the folder containing the .inf files (e.g. the version's extracted "
                        "package folder or its 'INF_INSTALL' subtree)."
                    ),
                },
                "recursive": {
                    "type": "boolean",
                    "description": (
                        "When true (default), also search nested subfolders for .inf files."
                    ),
                },
                "ignore_infs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of INF file names whose install failures should be treated as "
                        "non-fatal (recorded as 'ignored') instead of failing the overall result. "
                        "Matching is case-insensitive."
                    ),
                },
                "ignore_no_matching_device": {
                    "type": "boolean",
                    "description": (
                        "When true (default), an INF that is added to the store but matches no present "
                        "device is treated as 'ignored' rather than 'failed'."
                    ),
                },
            },
            "required": ["driver_folder"],
        },
    },
    {
        "name": "uninstall_bluetooth_driver",
        "description": (
            "Uninstall all Intel Bluetooth (IBT) driver packages from the Windows driver store via "
            "pnputil /delete-driver /uninstall /force, then verify removal. Requires Administrator. "
            "A reboot-required code (3010/1641) counts as success with 'reboot_required': true (reboot "
            "before reinstalling). A missing device instance (SPAPI_E_NO_SUCH_DEVINST, 0xE000020B) is a "
            "non-fatal WARNING listed in 'warnings', not a failure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_bluetooth_driver_version",
        "description": (
            "Get the currently installed Intel Bluetooth (IBT) driver version. Use before an install to "
            "decide whether an upgrade is needed. Key result fields: 'installed' (bool; false = none), "
            "'version' (e.g. \"24.40.0.3\", or null), 'versions' (all found), 'packages_count', and "
            "'drivers' (per-package details)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_wireless_module",
        "description": (
            "Detect the installed Intel wireless module (e.g. 'BE201') and the Bluetooth transport it "
            "uses. Result fields: 'module', 'transport' ('pci'/'usb'), 'transport_source', "
            "'recommended_inf' ('ibtpci.inf'/'ibtusb.inf'), and 'adapter_name'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

BLUETOOTH_DRIVER_INSTALL_TOOL_FUNCTIONS = {
    "install_bluetooth_driver": install_bluetooth_driver,
    "uninstall_bluetooth_driver": uninstall_bluetooth_driver,
    "get_bluetooth_driver_version": get_bluetooth_driver_version,
    "get_wireless_module": get_wireless_module,
}
