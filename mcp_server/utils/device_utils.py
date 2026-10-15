# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Device discovery, ADB resolution, and emulator management for MCP workflows."""

import os
import shutil
import subprocess
import sys
import time

from artemis.toolchain import toolchain


def resolve_adb_path() -> str:
    """Resolves the absolute path to the adb binary across platforms."""
    # 1. Check if adb is in PATH
    which_adb = shutil.which("adb")
    if which_adb:
        return which_adb

    # 2. Check ANDROID_HOME / ANDROID_SDK_ROOT
    for env_var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk_root = os.getenv(env_var)
        if sdk_root:
            candidate = os.path.join(
                sdk_root, "platform-tools", "adb.exe" if sys.platform == "win32" else "adb"
            )
            if os.path.exists(candidate):
                return candidate

    # 3. Check standard OS-specific SDK locations
    candidates = []
    if sys.platform == "win32":
        local_appdata = os.getenv("LOCALAPPDATA", "")
        if local_appdata:
            candidates.append(
                os.path.join(local_appdata, "Android", "Sdk", "platform-tools", "adb.exe")
            )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
                "/opt/homebrew/bin/adb",
                "/usr/local/bin/adb",
            ]
        )
    else:  # Linux
        candidates.extend(
            [
                "/usr/bin/adb",
                os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
                "/usr/local/bin/adb",
            ]
        )

    for cand in candidates:
        if os.path.exists(cand):
            return cand

    return "adb"


def resolve_emulator_path() -> str | None:
    """Use the shared SDK resolver, including custom SDKs discovered via adb."""
    return toolchain.resolve("emulator")


def get_connected_devices(adb_path: str | None = None) -> list[str]:
    """Returns a list of connected and authorized Android device serials."""
    adb = adb_path or resolve_adb_path()
    try:
        res = subprocess.run(
            [adb, "devices"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5,
        )
        devices: list[str] = []
        for line in res.stdout.splitlines():
            line_str = line.strip()
            if line_str and not line_str.startswith("List of devices"):
                parts = line_str.split()
                if len(parts) >= 2 and parts[1] == "device":
                    devices.append(parts[0])
        return devices
    except Exception:
        return []


def is_emulator_running(adb_path: str | None = None) -> bool:
    """Checks if an Android emulator (e.g. emulator-5554) is running and accessible."""
    adb = adb_path or resolve_adb_path()
    devices = get_connected_devices(adb)
    return any(d.startswith("emulator-") for d in devices)


def find_emulator_serial(adb_path: str, avd_name: str | None = None) -> str | None:
    """Find an authorized emulator, matching its AVD identity when requested."""
    serials = [
        serial for serial in get_connected_devices(adb_path) if serial.startswith("emulator-")
    ]
    if avd_name is None:
        return serials[0] if len(serials) == 1 else None
    for serial in serials:
        # Some emulator consoles return no name through `adb emu avd name`.
        # The boot property is available on current Google system images.
        for command in (
            ["shell", "getprop", "ro.boot.qemu.avd_name"],
            ["emu", "avd", "name"],
        ):
            try:
                result = subprocess.run(
                    [adb_path, "-s", serial, *command],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=True,
                )
            except (subprocess.SubprocessError, OSError):
                continue
            if result.stdout.strip().splitlines()[:1] == [avd_name]:
                return serial
    return None


def _wait_for_emulator_boot(
    adb: str,
    avd_name: str | None,
    timeout_seconds: int,
    proc: subprocess.Popen | None = None,
) -> str | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return None
        try:
            serial = find_emulator_serial(adb, avd_name)
            if serial is not None:
                result = subprocess.run(
                    [adb, "-s", serial, "shell", "getprop", "sys.boot_completed"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=True,
                )
                if result.stdout.strip() == "1":
                    return serial
        except (subprocess.SubprocessError, OSError):
            pass  # ADB can reconnect while the emulator boots.
        time.sleep(2)
    return None


def ensure_emulator_serial(
    avd_name: str | None = None,
    adb_path: str | None = None,
    emulator_path: str | None = None,
    timeout_seconds: int = 180,
) -> str | None:
    """Boot an explicitly chosen or sole installed AVD and return its actual serial.

    Multiple installed AVDs require an explicit name or ARTEMIS_AVD_NAME. Callers
    must surface the selection error so the user can choose before device actions.
    """
    adb = adb_path or resolve_adb_path()
    selected_avd = (avd_name or os.getenv("ARTEMIS_AVD_NAME") or "").strip() or None
    existing_serial = find_emulator_serial(adb, selected_avd)
    if existing_serial:
        return _wait_for_emulator_boot(adb, selected_avd, timeout_seconds)

    emu_exe = emulator_path or resolve_emulator_path()
    if not emu_exe or not os.path.exists(emu_exe):
        return None

    try:
        listed = subprocess.run(
            [emu_exe, "-list-avds"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    installed_avds = list(
        dict.fromkeys(line.strip() for line in listed.stdout.splitlines() if line.strip())
    )
    if selected_avd is None:
        if not installed_avds:
            raise RuntimeError(
                "No installed Android AVD found. Create an AVD before starting a task."
            )
        if len(installed_avds) != 1:
            raise RuntimeError(
                "Multiple Android AVDs are installed: "
                + ", ".join(installed_avds)
                + ". Ask the user to choose, then use mobile_diagnose(launch_avd='<name>') "
                "and pass its device_serial, or configure ARTEMIS_AVD_NAME."
            )
        selected_avd = installed_avds[0]
    elif selected_avd not in installed_avds:
        raise RuntimeError(
            f"Android AVD '{selected_avd}' is not installed. Available: {installed_avds}"
        )

    try:
        command = [emu_exe, "-avd", selected_avd]
        if sys.platform == "win32":
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS),
            )
        else:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except OSError:
        return None

    return _wait_for_emulator_boot(adb, selected_avd, timeout_seconds, proc)


def ensure_emulator(
    avd_name: str | None = None,
    adb_path: str | None = None,
    emulator_path: str | None = None,
    timeout_seconds: int = 180,
) -> bool:
    """Compatibility wrapper for callers that only need a readiness boolean."""
    return ensure_emulator_serial(avd_name, adb_path, emulator_path, timeout_seconds) is not None
