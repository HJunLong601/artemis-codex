# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""PID-aware cross-process port leases for managed local services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import socket
import time
import uuid

from artemis.config.paths import get_temp_dir
from artemis.runtime.process_probe import pid_is_alive


class PortLeaseError(RuntimeError):
    """Raised when no port in the requested range can be leased."""


def _process_created_at() -> float:
    try:
        import psutil

        return float(psutil.Process(os.getpid()).create_time())
    except Exception:
        return 0.0


def _port_is_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


@dataclass(slots=True)
class PortLease:
    """An owned port reservation released only by its matching token."""

    port: int
    host: str
    path: Path
    token: str
    _manager: PortLeaseManager = field(repr=False)
    _released: bool = field(default=False, init=False, repr=False)

    def release(self) -> None:
        if not self._released:
            self._manager.release(self)
            self._released = True

    def __enter__(self) -> PortLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class PortLeaseManager:
    """Coordinate port allocation between independent Artemis processes."""

    _MALFORMED_GRACE_SECONDS = 5.0

    def __init__(
        self,
        *,
        lease_dir: Path | None = None,
        availability_probe: Callable[[str, int], bool] | None = None,
    ):
        self._lease_dir = lease_dir or get_temp_dir("port-leases")
        self._lease_dir.mkdir(parents=True, exist_ok=True)
        self._availability_probe = availability_probe or _port_is_available

    def _path(self, host: str, port: int) -> Path:
        safe_host = host.replace(":", "_").replace("/", "_")
        return self._lease_dir / f"artemis-port-{safe_host}-{port}.lock"

    @staticmethod
    def _read(path: Path) -> dict[str, object] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _remove_if_stale(self, path: Path) -> bool:
        payload = self._read(path)
        if payload is None:
            try:
                if time.time() - path.stat().st_mtime < self._MALFORMED_GRACE_SECONDS:
                    return False
            except OSError:
                return True
        else:
            try:
                pid = int(payload["pid"])
                created_at = float(payload.get("process_created_at", 0.0))
            except (KeyError, TypeError, ValueError):
                pid = 0
                created_at = 0.0
            if pid_is_alive(pid, created_at):
                return False
        try:
            path.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def _try_create(self, host: str, port: int) -> PortLease | None:
        path = self._path(host, port)
        token = uuid.uuid4().hex
        payload = {
            "pid": os.getpid(),
            "process_created_at": _process_created_at(),
            "token": token,
            "host": host,
            "port": port,
            "created_at": time.time(),
        }
        for _attempt in range(2):
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if not self._remove_if_stale(path):
                    return None
                continue
            except OSError:
                return None
            try:
                os.write(fd, json.dumps(payload).encode("utf-8"))
            finally:
                os.close(fd)
            return PortLease(port=port, host=host, path=path, token=token, _manager=self)
        return None

    def acquire(self, start: int, end: int, *, host: str = "127.0.0.1") -> PortLease:
        if start <= 0 or end > 65535 or end < start:
            raise ValueError(f"Invalid port range: {start}-{end}")
        for port in range(start, end + 1):
            lease = self._try_create(host, port)
            if lease is None:
                continue
            if self._availability_probe(host, port):
                return lease
            lease.release()
        raise PortLeaseError(f"No available port in range {start}-{end} for host {host}.")

    def release(self, lease: PortLease) -> None:
        payload = self._read(lease.path)
        if payload is None or payload.get("token") != lease.token:
            return
        try:
            lease.path.unlink(missing_ok=True)
        except OSError as exc:
            raise PortLeaseError(f"Could not release port {lease.port}: {exc}") from exc

    def cleanup_stale_leases(self) -> int:
        removed = 0
        for path in self._lease_dir.glob("artemis-port-*.lock"):
            if self._remove_if_stale(path):
                removed += 1
        return removed
