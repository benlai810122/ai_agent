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


def uninstall_isst_driver(inf_name: str) -> dict:
    """Uninstall the ISST driver from the system using pnputil with force.

    Args:
        inf_name: The published driver name (e.g., 'oem123.inf') to uninstall.

    Returns:
        A dict with status and details of the uninstallation result.
    """
    try:
        if not inf_name or not inf_name.lower().endswith(".inf"):
            return {"error": "A valid .inf driver name must be provided (e.g., 'oem123.inf')."}

        # Use pnputil to delete the driver with force
        result = subprocess.run(
            ["pnputil", "/delete-driver", inf_name, "/force"],
            capture_output=True,
            text=True,
            timeout=120,
        )

        output = result.stdout.strip()
        err_output = result.stderr.strip()

        if result.returncode == 0:
            return {
                "status": "success",
                "message": "ISST driver uninstalled successfully.",
                "output": output,
            }
        else:
            return {
                "status": "failed",
                "message": "ISST driver uninstallation failed.",
                "return_code": result.returncode,
                "output": output,
                "error_output": err_output,
            }
    except subprocess.TimeoutExpired:
        return {"error": "Driver uninstallation timed out after 120 seconds."}
    except Exception as e:
        return {"error": str(e)}


def check_isst_driver_status() -> dict:
    """Check the current installation status of the ISST driver."""
    try:
        # TODO: Implement ISST driver status check logic
        return {"status": "success", "installed": False, "message": "ISST driver status check not yet implemented."}
    except Exception as e:
        return {"error": str(e)}
