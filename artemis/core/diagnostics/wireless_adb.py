"""Explicit USB-to-Wi-Fi setup using the host's discovered ADB executable."""

import asyncio
import ipaddress
import os
import re
import subprocess
import time

from artemis.core.diagnostics.adb_server_connection import adb_server_connection
from artemis.runtime.device_lock import DeviceBusyError, DeviceExecutionLock
from artemis.toolchain import toolchain


async def auto_connect_wireless(serial: str | None = None) -> dict:
    """Discover authorized USB targets, enable TCP/IP, and verify the same phone.

    No host/IP is accepted from the caller. Multiple targets require a second
    explicit request with a listed serial. Each subprocess is bounded; device
    ownership is released even on failure. Never select a task transport here.
    """
    endpoint = adb_server_connection.current_endpoint()
    if not endpoint.is_local_default:
        return {"success": False, "message": "Switch to local ADB before automatic connection."}
    adb = toolchain.resolve("adb") or "adb"
    environment = endpoint.apply_to_environment(os.environ.copy())

    def run(*args: str, timeout: float = 5) -> str:
        result = subprocess.run(
            [adb, *args], capture_output=True, text=True, timeout=timeout, env=environment
        )
        if result.returncode:
            raise RuntimeError("ADB command failed. Check USB authorization and the Wi-Fi network.")
        return result.stdout.strip()

    def connect() -> dict:
        lease = None
        wireless_lease = None
        try:
            candidates = []
            connected = set()
            for line in run("devices", "-l").splitlines():
                fields = line.split()
                if len(fields) < 2 or fields[1] != "device":
                    continue
                target = fields[0]
                connected.add(target)
                if ":" in target or target.startswith("emulator-") or "._tcp" in target:
                    continue
                model = next((v[6:] for v in fields[2:] if v.startswith("model:")), target)
                candidates.append({"serial": target, "model": model})
            if not candidates:
                return {"success": False, "message": "Connect and authorize a USB phone first."}
            if serial is None and len(candidates) > 1:
                return {
                    "success": False,
                    "message": "Choose the USB phone to connect.",
                    "candidates": candidates,
                }
            source = serial or candidates[0]["serial"]
            if source not in {c["serial"] for c in candidates}:
                return {
                    "success": False,
                    "message": "The selected USB phone is no longer available.",
                }
            lease = DeviceExecutionLock(
                source, "Automatic Wi-Fi ADB setup", lock_scope=endpoint.identity
            )
            lease.acquire(blocking=False)
            if run("-s", source, "shell", "getprop", "ro.kernel.qemu") == "1":
                return {
                    "success": False,
                    "message": "Automatic Wi-Fi setup requires a physical USB phone.",
                }
            identity = run("-s", source, "shell", "getprop", "ro.serialno")
            if not identity:
                return {"success": False, "message": "Could not verify the USB phone identity."}
            addresses = set()
            for line in run("-s", source, "shell", "ip", "-o", "-4", "addr", "show").splitlines():
                match = re.search(
                    r"^\d+:\s+((?:wlan|wifi|swlan|wlp)[\w.-]*)\s+inet\s+([\d.]+)/\d+.*\bscope global\b",
                    line,
                )
                if match:
                    address = ipaddress.IPv4Address(match[2])
                    if not (
                        address.is_loopback
                        or address.is_link_local
                        or address.is_multicast
                        or address.is_unspecified
                    ):
                        addresses.add(str(address))
            if len(addresses) != 1:
                return {
                    "success": False,
                    "message": "Could not identify one Wi-Fi address. Connect the phone to Wi-Fi or use manual connection.",
                }
            host = addresses.pop()
            target = f"{host}:5555"
            wireless_lease = DeviceExecutionLock(
                target, "Automatic Wi-Fi ADB setup", lock_scope=endpoint.identity
            )
            wireless_lease.acquire(blocking=False)

            def verify_connection() -> dict:
                if run("-s", target, "get-state") != "device":
                    raise RuntimeError("Wireless transport is not ready")
                if run("-s", target, "shell", "getprop", "ro.serialno") != identity:
                    return {
                        "success": False,
                        "message": "Wi-Fi device identity did not match the USB phone.",
                    }
                return {
                    "success": True,
                    "message": "Wi-Fi ADB connected and verified. You can unplug USB.",
                    "host": host,
                    "port": 5555,
                    "serial": target,
                }

            if target in connected:
                try:
                    return verify_connection()
                except (RuntimeError, subprocess.TimeoutExpired):
                    pass  # Stale transport: perform the explicit setup below.
            run("-s", source, "tcpip", "5555", timeout=10)
            # adbd restarts after tcpip. Connect attempts are bounded and never
            # enable the listener a second time or disconnect other transports.
            for attempt in range(3):
                if attempt:
                    time.sleep(0.5)
                try:
                    output = run("connect", target)
                    if "connected to" in output.lower():
                        return verify_connection()
                except (RuntimeError, subprocess.TimeoutExpired):
                    continue
            return {
                "success": False,
                "message": "Wi-Fi connection failed. Keep USB connected, check the network, or use manual pairing. TCP/IP debugging may remain enabled.",
            }
        except DeviceBusyError:
            return {
                "success": False,
                "message": "The USB phone is busy. Wait for its task to finish.",
            }
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError):
            return {
                "success": False,
                "message": "Automatic connection failed. Check USB authorization and Wi-Fi. TCP/IP debugging may remain enabled.",
            }
        finally:
            if wireless_lease is not None:
                wireless_lease.release()
            if lease is not None:
                lease.release()

    return await asyncio.to_thread(connect)
