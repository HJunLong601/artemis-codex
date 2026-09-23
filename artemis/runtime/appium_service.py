# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Managed and external Appium service lifecycle support."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.request

from artemis.config.paths import get_temp_dir
from artemis.runtime.port_lease import PortLease, PortLeaseManager
from artemis.runtime.supervisor import ProcessSupervisor


class AppiumServiceError(RuntimeError):
    """Base error for Appium lifecycle failures."""


class AppiumServiceStartError(AppiumServiceError):
    """Raised when Appium cannot start or pass its health check."""


@dataclass(frozen=True, slots=True)
class AppiumServiceConfig:
    """Configuration for a managed local or externally hosted Appium service."""

    server_url: str | None = None
    executable: str = "appium"
    host: str = "127.0.0.1"
    port_start: int = 4723
    port_end: int = 4823
    base_path: str = "/"
    startup_timeout: float = 30.0
    health_timeout: float = 1.0
    poll_interval: float = 0.25


@dataclass(frozen=True, slots=True)
class AppiumServiceHandle:
    """Ready Appium endpoint and its ownership metadata."""

    url: str
    managed: bool
    port: int | None = None
    log_path: Path | None = None


HealthProbe = Callable[[str, float], bool]
ProcessFactory = Callable[[list[str], Path], Awaitable[Any]]
ProcessStopper = Callable[[Any], Awaitable[None]]


def _status_url(server_url: str) -> str:
    return f"{server_url.rstrip('/')}/status"


def _default_health_probe(server_url: str, timeout: float) -> bool:
    request = urllib.request.Request(
        _status_url(server_url),
        headers={"User-Agent": "Artemis-Appium-Lifecycle/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return False
    value = payload.get("value") if isinstance(payload, dict) else None
    return isinstance(value, dict) and value.get("ready") is True


async def _default_process_factory(command: list[str], _log_path: Path) -> Any:
    return await ProcessSupervisor.spawn_async(command, capture_output=True)


async def _default_process_stopper(process: Any) -> None:
    await ProcessSupervisor.stop_process(process)


class AppiumServiceManager:
    """Own a local Appium process or validate a user-managed endpoint."""

    def __init__(
        self,
        config: AppiumServiceConfig | None = None,
        *,
        port_manager: PortLeaseManager | None = None,
        health_probe: HealthProbe | None = None,
        process_factory: ProcessFactory | None = None,
        process_stopper: ProcessStopper | None = None,
        log_dir: Path | None = None,
    ):
        self.config = config or AppiumServiceConfig()
        self._port_manager = port_manager or PortLeaseManager()
        self._health_probe = health_probe or _default_health_probe
        self._process_factory = process_factory or _default_process_factory
        self._process_stopper = process_stopper or _default_process_stopper
        self._log_dir = log_dir or get_temp_dir("appium")
        self._state_lock = asyncio.Lock()
        self._handle: AppiumServiceHandle | None = None
        self._process: Any | None = None
        self._lease: PortLease | None = None
        self._log_tasks: list[asyncio.Task[None]] = []

    async def _is_ready(self, url: str) -> bool:
        return await asyncio.to_thread(self._health_probe, url, self.config.health_timeout)

    async def _drain_stream(self, stream: Any, log_path: Path, label: str) -> None:
        if stream is None:
            return
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as output:
            while True:
                line = await stream.readline()
                if not line:
                    return
                text = line.decode(errors="replace") if isinstance(line, bytes) else str(line)
                output.write(f"[{label}] {text}")
                output.flush()

    async def _wait_until_ready(self, url: str) -> None:
        deadline = time.monotonic() + max(self.config.startup_timeout, 0.0)
        while True:
            if await self._is_ready(url):
                return
            if self._process is not None and self._process.returncode is not None:
                raise AppiumServiceStartError(
                    f"Managed Appium exited before becoming ready (code {self._process.returncode})."
                )
            if time.monotonic() >= deadline:
                raise AppiumServiceStartError(
                    f"Appium did not become ready within {self.config.startup_timeout} seconds."
                )
            await asyncio.sleep(self.config.poll_interval)

    async def _cleanup_managed(self) -> None:
        process = self._process
        self._process = None
        if process is not None:
            await self._process_stopper(process)
        for task in self._log_tasks:
            task.cancel()
        if self._log_tasks:
            await asyncio.gather(*self._log_tasks, return_exceptions=True)
        self._log_tasks.clear()
        lease = self._lease
        self._lease = None
        if lease is not None:
            lease.release()

    async def start(self) -> AppiumServiceHandle:
        async with self._state_lock:
            if self._handle is not None:
                return self._handle
            if self.config.server_url:
                url = self.config.server_url.rstrip("/")
                if not await self._is_ready(url):
                    raise AppiumServiceStartError(f"External Appium is not ready at {url}.")
                self._handle = AppiumServiceHandle(url=url, managed=False)
                return self._handle

            self._lease = self._port_manager.acquire(
                self.config.port_start,
                self.config.port_end,
                host=self.config.host,
            )
            port = self._lease.port
            url = f"http://{self.config.host}:{port}"
            log_path = self._log_dir / f"appium-{port}.log"
            command = [
                self.config.executable,
                "--address",
                self.config.host,
                "--port",
                str(port),
                "--base-path",
                self.config.base_path,
            ]
            try:
                self._process = await self._process_factory(command, log_path)
                for label, stream in (
                    ("stdout", getattr(self._process, "stdout", None)),
                    ("stderr", getattr(self._process, "stderr", None)),
                ):
                    if stream is not None:
                        self._log_tasks.append(
                            asyncio.create_task(self._drain_stream(stream, log_path, label))
                        )
                await self._wait_until_ready(url)
            except Exception:
                await self._cleanup_managed()
                raise
            self._handle = AppiumServiceHandle(
                url=url,
                managed=True,
                port=port,
                log_path=log_path,
            )
            return self._handle

    async def stop(self) -> None:
        async with self._state_lock:
            handle = self._handle
            self._handle = None
            if handle is None or not handle.managed:
                return
            await self._cleanup_managed()

    async def __aenter__(self) -> AppiumServiceHandle:
        return await self.start()

    async def __aexit__(self, *_args: object) -> None:
        await self.stop()
