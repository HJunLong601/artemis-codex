"""Verify device selection and explicitly disconnect individual wireless transports."""

import asyncio
import os
import subprocess

from artemis.core.diagnostics.adb_server_connection import adb_server_connection
from artemis.runtime.device_lock import DeviceBusyError, DeviceExecutionLock
from artemis.toolchain import toolchain


def is_wireless_serial(serial: str) -> bool:
    return ":" in serial or "._tcp" in serial


async def change_device_connection(serial: str, *, disconnect: bool = False) -> dict:
    """Verify an exact inventoried serial; prune only confirmed unavailable targets.

    Selection leaves usable but slow devices listed. Disconnection is limited
    to one wireless serial, guarded by its task lock; USB/emulators are never
    stopped. The selected ADB endpoint is frozen throughout the operation.
    """
    endpoint = adb_server_connection.current_endpoint()
    adb = toolchain.resolve("adb") or "adb"
    environment = endpoint.apply_to_environment(os.environ.copy())

    def run(*args):
        return subprocess.run(
            [adb, *args], capture_output=True, text=True, timeout=5, env=environment
        )

    def inventory():
        result = run("devices", "-l")
        if result.returncode:
            raise RuntimeError("ADB inventory unavailable")
        return {
            parts[0]: parts[1]
            for line in result.stdout.splitlines()
            if len(parts := line.split()) >= 2
            and parts[1] in {"device", "offline", "unauthorized", "recovery", "sideload"}
        }

    def remove_wireless() -> bool:
        lease = DeviceExecutionLock(serial, "Disconnect Wi-Fi ADB", lock_scope=endpoint.identity)
        lease.acquire(blocking=False)
        try:
            result = run("disconnect", serial)
            return result.returncode == 0 and serial not in inventory()
        finally:
            lease.release()

    def change():
        try:
            if not serial or serial.startswith("-") or len(serial) > 256:
                return {"success": False, "message": "Invalid device serial."}
            devices = inventory()
            if disconnect and not is_wireless_serial(serial):
                return {
                    "success": False,
                    "message": "Unplug the USB cable to remove a USB phone. Stop emulators from their controls.",
                }
            if serial not in devices:
                return {
                    "success": disconnect,
                    "removed_serial": serial,
                    "message": "Device is no longer connected and was removed from the list.",
                }
            if disconnect:
                removed = remove_wireless()
                return {
                    "success": removed,
                    "removed_serial": serial if removed else None,
                    "message": "Wireless device disconnected."
                    if removed
                    else "Disconnection failed. The device is still listed.",
                }
            if devices[serial] == "device":
                state = run("-s", serial, "get-state")
                identity = (
                    run("-s", serial, "shell", "getprop", "ro.serialno")
                    if state.returncode == 0 and state.stdout.strip() == "device"
                    else None
                )
                if identity is not None and identity.returncode == 0 and identity.stdout.strip():
                    return {"success": True, "selected_serial": serial}
                devices = inventory()
            unavailable = devices.get(serial) != "device"
            removed = serial not in devices or (
                unavailable and is_wireless_serial(serial) and remove_wireless()
            )
            return {
                "success": False,
                "removed_serial": serial if removed else None,
                "message": "Device is offline or unauthorized. Check the phone authorization prompt and reconnect."
                if unavailable
                else "Device verification failed. The current device was kept; retry when it is ready.",
            }
        except DeviceBusyError:
            return {
                "success": False,
                "message": "Device is running a task. Wait for it to finish before disconnecting.",
            }
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return {
                "success": False,
                "message": "Device operation failed. Check the connection and retry; the current device was kept.",
            }

    return await asyncio.to_thread(change)
