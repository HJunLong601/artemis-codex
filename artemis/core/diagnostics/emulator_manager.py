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

"""Android Emulator Launch & Boot Progress Lifecycle Manager."""

import asyncio
from collections import deque
from enum import Enum
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

from pydantic import BaseModel, Field

from artemis.toolchain import toolchain
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)


class EmulatorLaunchStage(str, Enum):
    """Lifecycle stages of launching an Android Virtual Device."""

    IDLE = "idle"
    STARTING = "starting"  # Process spawned, verifying PID
    WAITING_FOR_ADB = "waiting_for_adb"  # Process alive, waiting for emulator to appear in ADB
    BOOTING = "booting"  # Visible in ADB, waiting for sys.boot_completed=1
    READY = "ready"  # Boot complete and ready for automation
    FAILED = "failed"  # Process crashed, lock error, or timeout
    STOPPED = "stopped"  # Stopped by user


class EmulatorLaunchState(BaseModel):
    """State schema for real-time emulator launch tracking."""

    avd_name: str | None = None
    status: EmulatorLaunchStage = EmulatorLaunchStage.IDLE
    pid: int | None = None
    serial: str | None = None
    stage_message: str = "Ready to launch"
    progress_percent: int = 0
    started_at: float | None = None
    elapsed_seconds: int = 0
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    can_retry: bool = True


class EmulatorManager:
    """Manages background emulator execution, stdout/stderr stream capture, and boot completion polling."""

    def __init__(self):
        self._current_state = EmulatorLaunchState()
        self._proc: subprocess.Popen | None = None
        self._log_buffer: deque[str] = deque(maxlen=100)
        self._track_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._reader_thread: threading.Thread | None = None

    @staticmethod
    def _subprocess_creation_kwargs() -> dict[str, Any]:
        """Isolate the emulator from the UI server's console signals.

        ``start_new_session`` is POSIX-only and is ignored by Python's Windows
        subprocess implementation. Windows therefore needs explicit creation
        flags so Ctrl+C or shutdown signals sent to the UI server do not also
        terminate the emulator. The Linux/macOS behavior remains unchanged.
        """
        if sys.platform == "win32":
            return {
                "creationflags": (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
            }
        return {"start_new_session": True}

    def _locate_emulator(self) -> str | None:
        """Use the same SDK resolution as diagnostics and MCP task startup."""
        return toolchain.resolve("emulator")

    def _locate_adb(self) -> str:
        """Find the adb binary path."""
        resolved = toolchain.resolve("adb")
        if resolved:
            return resolved
        return shutil.which("adb") or "adb"

    async def _adb_output(self, adb_path: str, *args: str) -> str:
        """Bound every boot-time ADB query so an unresponsive device cannot stall tracking."""
        proc = await asyncio.create_subprocess_exec(
            adb_path, *args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            if proc.returncode is None:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass  # It may have exited between the timeout and kill.
            await proc.communicate()
            raise
        if proc.returncode != 0:
            raise RuntimeError(f"ADB query failed: {stderr.decode(errors='replace').strip()[:240]}")
        return stdout.decode(errors="replace").strip()

    async def _find_running_avd(self, adb_path: str, avd_name: str) -> str | None:
        listing = await self._adb_output(adb_path, "devices", "-l")
        serials = [
            parts[0]
            for line in listing.splitlines()
            if len(parts := line.split()) >= 2
            and parts[0].startswith("emulator-")
            and parts[1] == "device"
        ]

        async def matches(serial: str) -> bool:
            name = await self._adb_output(
                adb_path, "-s", serial, "shell", "getprop", "ro.boot.qemu.avd_name"
            )
            if not name:
                name = await self._adb_output(adb_path, "-s", serial, "emu", "avd", "name")
            return name.splitlines()[:1] == [avd_name]

        matched = await asyncio.gather(*(matches(serial) for serial in serials))
        selected = [serial for serial, match in zip(serials, matched, strict=True) if match]
        if len(selected) > 1:
            raise RuntimeError(
                f"Multiple running instances of AVD '{avd_name}': {selected}. Choose a device serial."
            )
        if not selected and any(
            len(parts := line.split()) >= 2
            and parts[0].startswith("emulator-")
            and parts[1] != "device"
            for line in listing.splitlines()
        ):
            raise RuntimeError(
                "An emulator is still connecting to ADB. Retry after its identity is available."
            )
        return selected[0] if selected else None

    async def _boot_services_ready(self, adb_path: str, serial: str) -> bool:
        try:
            if (
                await self._adb_output(
                    adb_path, "-s", serial, "shell", "getprop", "sys.boot_completed"
                )
                != "1"
            ):
                return False
            packages = await self._adb_output(
                adb_path, "-s", serial, "shell", "pm", "path", "android"
            )
        except (OSError, RuntimeError, TimeoutError):
            return False
        return any(line.startswith("package:") for line in packages.splitlines())

    def _mark_ready(
        self, avd_name: str, serial: str, proc: subprocess.Popen | None, started_at: float
    ) -> None:
        self._current_state = EmulatorLaunchState(
            avd_name=avd_name,
            status=EmulatorLaunchStage.READY,
            pid=proc.pid if proc is not None else None,
            serial=serial,
            stage_message=f"Android emulator '{avd_name}' is ready (system and package services verified).",
            progress_percent=100,
            started_at=started_at,
            elapsed_seconds=int(time.time() - started_at),
            logs=list(self._log_buffer),
        )
        try:
            from artemis.core.diagnostics.engine import readiness_engine

            readiness_engine.set_probe_target_serial(serial)
        except Exception as exc:
            logger.debug(f"Could not update the diagnostics target: {exc}")

    def get_status(self) -> EmulatorLaunchState:
        """Return current launch progress state snapshot."""
        if (
            self._current_state.status == EmulatorLaunchStage.READY
            and self._proc is not None
            and self._proc.poll() is not None
        ):
            self._current_state.status = EmulatorLaunchStage.FAILED
            self._current_state.error = "The emulator process has exited."
            self._current_state.stage_message = "Emulator process is no longer running."
            self._current_state.progress_percent = 0
        state = self._current_state.model_copy()
        if state.started_at and state.status in (
            EmulatorLaunchStage.STARTING,
            EmulatorLaunchStage.WAITING_FOR_ADB,
            EmulatorLaunchStage.BOOTING,
        ):
            state.elapsed_seconds = int(time.time() - state.started_at)
        state.logs = list(self._log_buffer)
        return state

    def _stream_logs(self, proc: subprocess.Popen):
        """Continuously read process stdout/stderr in a background thread."""
        try:
            if proc.stdout:
                for line in iter(proc.stdout.readline, ""):
                    if not line:
                        break
                    clean_line = line.strip()
                    if clean_line:
                        self._log_buffer.append(clean_line)
        except Exception as e:
            logger.debug(f"Log streaming finished: {e}")
        finally:
            if proc.stdout:
                proc.stdout.close()

    async def launch(self, avd_name: str) -> EmulatorLaunchState:
        """Initiate background emulator launch and start asynchronous boot monitoring."""
        clean_avd = avd_name.strip()
        if not clean_avd:
            self._current_state = EmulatorLaunchState(
                status=EmulatorLaunchStage.FAILED,
                error="AVD name cannot be empty.",
                stage_message="Launch failed: AVD name cannot be empty.",
            )
            return self.get_status()

        async with self._lock:
            tracking = self._track_task is not None and not self._track_task.done()
            owned_running = self._proc is not None and self._proc.poll() is None
            if tracking or owned_running:
                if self._current_state.avd_name == clean_avd and tracking:
                    return self.get_status()
                if self._current_state.avd_name != clean_avd:
                    return EmulatorLaunchState(
                        avd_name=clean_avd,
                        status=EmulatorLaunchStage.FAILED,
                        error=f"Already managing AVD '{self._current_state.avd_name}'. Stop it before launching another AVD.",
                        stage_message="Another AVD is already managed; its state was preserved.",
                    )

            try:
                adb_path = self._locate_adb()
                existing = await self._find_running_avd(adb_path, clean_avd)
                if existing:
                    proc = self._proc if owned_running else None
                    self._proc = proc
                    now = time.time()
                    self._log_buffer.append(
                        f"Reusing running AVD '{clean_avd}' on {existing}; no process spawned."
                    )
                    if await self._boot_services_ready(adb_path, existing):
                        self._mark_ready(clean_avd, existing, proc, now)
                    else:
                        self._current_state = EmulatorLaunchState(
                            avd_name=clean_avd,
                            status=EmulatorLaunchStage.BOOTING,
                            serial=existing,
                            pid=proc.pid if proc is not None else None,
                            stage_message="Reusing existing AVD; waiting for Android system services.",
                            started_at=now,
                            progress_percent=65,
                        )
                        self._track_task = asyncio.create_task(
                            self._track_boot_lifecycle(
                                clean_avd, proc, now, existing_serial=existing
                            )
                        )
                    return self.get_status()
            except (OSError, RuntimeError, TimeoutError) as exc:
                self._current_state = EmulatorLaunchState(
                    avd_name=clean_avd,
                    status=EmulatorLaunchStage.FAILED,
                    error=f"Cannot verify running emulator identity: {exc}",
                    stage_message="ADB discovery failed; no new emulator was started.",
                )
                return self.get_status()

            if owned_running:
                return EmulatorLaunchState(
                    avd_name=clean_avd,
                    status=EmulatorLaunchStage.FAILED,
                    error="The managed emulator process is still alive but its ADB identity is unavailable. Wait or stop it before retrying.",
                    stage_message="Existing process preserved; no duplicate emulator started.",
                )

            emu_path = self._locate_emulator()
            if not emu_path:
                self._current_state = EmulatorLaunchState(
                    avd_name=clean_avd,
                    status=EmulatorLaunchStage.FAILED,
                    error="Android emulator executable not found in PATH or Android SDK. Please install Android SDK platform tools and emulator.",
                    stage_message="Emulator binary not found.",
                )
                return self.get_status()

            self._log_buffer.clear()
            self._log_buffer.append(f"Starting emulator '{clean_avd}' using binary: {emu_path}")

            try:
                # Cancel previous tracker if any
                if self._track_task and not self._track_task.done():
                    self._track_task.cancel()

                # Spawn emulator process capturing stdout & stderr
                proc = subprocess.Popen(
                    [emu_path, "-avd", clean_avd],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    **self._subprocess_creation_kwargs(),
                )
                self._proc = proc
                now = time.time()

                # Start reader thread for stdout/stderr
                self._reader_thread = threading.Thread(
                    target=self._stream_logs, args=(proc,), daemon=True
                )
                self._reader_thread.start()

                self._current_state = EmulatorLaunchState(
                    avd_name=clean_avd,
                    status=EmulatorLaunchStage.STARTING,
                    pid=proc.pid,
                    stage_message=f"Process spawned (PID: {proc.pid}). Initializing QEMU virtualization...",
                    progress_percent=15,
                    started_at=now,
                    elapsed_seconds=0,
                    error=None,
                    logs=list(self._log_buffer),
                )

                # Launch async boot tracker task
                self._track_task = asyncio.create_task(
                    self._track_boot_lifecycle(clean_avd, proc, now)
                )

                return self.get_status()

            except Exception as e:
                logger.error(f"Failed to spawn emulator process: {e}")
                self._current_state = EmulatorLaunchState(
                    avd_name=clean_avd,
                    status=EmulatorLaunchStage.FAILED,
                    error=f"Failed to spawn emulator process: {e}",
                    stage_message="Failed to spawn emulator process.",
                    logs=list(self._log_buffer),
                )
                return self.get_status()

    async def _track_boot_lifecycle(
        self,
        avd_name: str,
        proc: subprocess.Popen | None,
        started_at: float,
        *,
        existing_serial: str | None = None,
    ):
        """Monitor emulator lifecycle from process execution to ADB connection and OS boot completion."""
        adb_path = self._locate_adb()
        detected_serial: str | None = existing_serial
        max_wait_seconds = 180  # 3 minutes maximum boot timeout

        try:
            # Phase 1: Early crash detection (first 5 seconds)
            for _ in range(5 if proc is not None else 0):
                await asyncio.sleep(1)
                poll_res = proc.poll() if proc is not None else None
                if poll_res is not None:
                    logs_str = "\n".join(list(self._log_buffer)[-10:])
                    error_msg = f"Emulator process exited immediately with code {poll_res}."
                    if "already running" in logs_str.lower() or "lock" in logs_str.lower():
                        error_msg = f"AVD '{avd_name}' is locked by another running emulator instance. Try stopping existing instances or restart ADB."
                    elif "panic" in logs_str.lower():
                        error_msg = f"Emulator panic: {logs_str}"

                    logger.error(f"[EmulatorManager] Early crash: {error_msg}")
                    self._current_state = EmulatorLaunchState(
                        avd_name=avd_name,
                        status=EmulatorLaunchStage.FAILED,
                        pid=proc.pid if proc is not None else None,
                        error=error_msg,
                        stage_message=f"Process exited prematurely (Exit code {poll_res})",
                        started_at=started_at,
                        elapsed_seconds=int(time.time() - started_at),
                        logs=list(self._log_buffer),
                    )
                    return

            # Phase 2: Waiting for ADB handshake
            self._current_state.status = EmulatorLaunchStage.WAITING_FOR_ADB
            self._current_state.progress_percent = 35
            self._current_state.stage_message = (
                "QEMU hypervisor started. Waiting for ADB connection..."
            )

            while not detected_serial and time.time() - started_at < max_wait_seconds:
                await asyncio.sleep(1.5)

                # Check if process died
                poll_res = proc.poll() if proc is not None else None
                if poll_res is not None:
                    logs_str = "\n".join(list(self._log_buffer)[-10:])
                    self._current_state = EmulatorLaunchState(
                        avd_name=avd_name,
                        status=EmulatorLaunchStage.FAILED,
                        pid=proc.pid if proc is not None else None,
                        error=f"Emulator process terminated unexpectedly (exit code {poll_res}): {logs_str}",
                        stage_message="Process terminated unexpectedly",
                        started_at=started_at,
                        elapsed_seconds=int(time.time() - started_at),
                        logs=list(self._log_buffer),
                    )
                    return

                # Check adb devices for emulator serial
                try:
                    out_text = await self._adb_output(adb_path, "devices", "-l")

                    # Scan for emulator-*
                    for line in out_text.splitlines():
                        line = line.strip()
                        if line.startswith("emulator-"):
                            parts = line.split()
                            if len(parts) < 2 or parts[1] != "device":
                                continue
                            serial = parts[0]
                            name = await self._adb_output(
                                adb_path, "-s", serial, "shell", "getprop", "ro.boot.qemu.avd_name"
                            )
                            if not name:
                                name = await self._adb_output(
                                    adb_path, "-s", serial, "emu", "avd", "name"
                                )
                            if name.splitlines()[:1] == [avd_name]:
                                detected_serial = serial
                                break
                except Exception as e:
                    logger.debug(f"ADB query error during boot tracking: {e}")

                if detected_serial:
                    break

            if not detected_serial:
                self._current_state = EmulatorLaunchState(
                    avd_name=avd_name,
                    status=EmulatorLaunchStage.FAILED,
                    pid=proc.pid if proc is not None else None,
                    error="Timeout waiting for emulator to connect to ADB.",
                    stage_message="ADB handshake timed out.",
                    started_at=started_at,
                    elapsed_seconds=int(time.time() - started_at),
                    logs=list(self._log_buffer),
                )
                return

            # Phase 3: Android OS Booting (Polling sys.boot_completed)
            self._current_state.status = EmulatorLaunchStage.BOOTING
            self._current_state.serial = detected_serial
            self._current_state.progress_percent = 65
            self._current_state.stage_message = (
                f"Connected to ADB ({detected_serial}). Android OS is booting up..."
            )

            while time.time() - started_at < max_wait_seconds:
                await asyncio.sleep(2)

                # Check if process died
                poll_res = proc.poll() if proc is not None else None
                if poll_res is not None:
                    self._current_state = EmulatorLaunchState(
                        avd_name=avd_name,
                        status=EmulatorLaunchStage.FAILED,
                        pid=proc.pid if proc is not None else None,
                        error=f"Emulator process crashed during OS boot (exit code {poll_res}).",
                        stage_message="Process crashed during OS boot",
                        started_at=started_at,
                        elapsed_seconds=int(time.time() - started_at),
                        logs=list(self._log_buffer),
                    )
                    return

                # Require both Android boot completion and a working package service.
                try:
                    if await self._boot_services_ready(adb_path, detected_serial):
                        self._mark_ready(avd_name, detected_serial, proc, started_at)
                        return
                    else:
                        # Gradual progress visual indicator
                        elapsed = time.time() - started_at
                        calc_progress = min(95, int(65 + (elapsed / 60) * 30))
                        self._current_state.progress_percent = calc_progress
                        self._current_state.stage_message = f"Android OS booting ({int(elapsed)}s)... Initializing system services..."
                except Exception as e:
                    logger.debug(f"Error querying boot_completed: {e}")

            # Timeout after max_wait_seconds
            self._current_state = EmulatorLaunchState(
                avd_name=avd_name,
                status=EmulatorLaunchStage.FAILED,
                pid=proc.pid if proc is not None else None,
                serial=detected_serial,
                error=f"Android OS boot did not finish within {max_wait_seconds}s timeout.",
                stage_message="Boot process timed out.",
                started_at=started_at,
                elapsed_seconds=int(time.time() - started_at),
                logs=list(self._log_buffer),
            )

        except asyncio.CancelledError:
            logger.info(f"[EmulatorManager] Boot tracker cancelled for '{avd_name}'")
        except Exception as exc:
            logger.error(f"[EmulatorManager] Unexpected exception in boot tracker: {exc}")
            self._current_state = EmulatorLaunchState(
                avd_name=avd_name,
                status=EmulatorLaunchStage.FAILED,
                pid=proc.pid if proc else None,
                error=f"Unexpected error: {exc}",
                stage_message="Unexpected error during boot tracking.",
                started_at=started_at,
                elapsed_seconds=int(time.time() - started_at),
                logs=list(self._log_buffer),
            )

    async def stop(self) -> dict[str, Any]:
        """Terminate the active emulator process."""
        async with self._lock:
            if self._track_task and not self._track_task.done():
                self._track_task.cancel()

            serial = self._current_state.serial
            pid = self._current_state.pid
            avd = self._current_state.avd_name

            if serial:
                try:
                    adb_path = self._locate_adb()
                    subprocess.run(
                        [adb_path, "-s", serial, "emu", "kill"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                except (OSError, subprocess.SubprocessError):
                    # Graceful `adb emu kill` failed or timed out; the direct
                    # process terminate/kill below still stops the emulator.
                    pass

            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                except Exception as e:
                    logger.error(f"Error terminating emulator process {pid}: {e}")

            self._current_state = EmulatorLaunchState(
                avd_name=avd,
                status=EmulatorLaunchStage.STOPPED,
                stage_message="Emulator process stopped by user.",
                logs=list(self._log_buffer),
            )
            return {"success": True, "message": f"Emulator '{avd}' stopped"}

    def dismiss(self) -> dict[str, Any]:
        """Reset emulator launch state back to idle."""
        if self._track_task is not None and not self._track_task.done():
            return {
                "success": False,
                "message": "Boot tracking is active; stop it before resetting state.",
            }
        self._current_state = EmulatorLaunchState()
        self._log_buffer.clear()
        return {"success": True, "message": "State reset"}


# Global singleton instance
emulator_manager = EmulatorManager()
