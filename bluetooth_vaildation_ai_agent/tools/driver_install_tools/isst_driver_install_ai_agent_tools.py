import os
import subprocess


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

        # Use pnputil to add and install the driver
        result = subprocess.run(
            ["pnputil", "/add-driver", inf_path, "/install"],
            capture_output=True,
            text=True,
            timeout=120,
        )

        output = result.stdout.strip()
        err_output = result.stderr.strip()

        if result.returncode == 0:
            return {
                "status": "success",
                "message": "ISST driver installed successfully.",
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
                "message": "No ISST driver found on this system.",
                "drivers": [],
            }

        return {
            "status": "success",
            "installed": True,
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
            "deciding whether an install or upgrade is needed."
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
    "uninstall_isst_driver": uninstall_isst_driver,
    "get_isst_driver_version": get_isst_driver_version,
}
