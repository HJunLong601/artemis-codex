from pathlib import Path
from types import SimpleNamespace

import pytest

from artemis.runtime.appium_service import (
    AppiumServiceConfig,
    AppiumServiceManager,
    AppiumServiceStartError,
)


class FakeLease:
    def __init__(self, port: int):
        self.port = port
        self.released = False

    def release(self) -> None:
        self.released = True


class FakePortManager:
    def __init__(self, port: int = 4723):
        self.lease = FakeLease(port)

    def acquire(self, _start: int, _end: int, *, host: str = "127.0.0.1") -> FakeLease:
        assert host == "127.0.0.1"
        return self.lease


@pytest.mark.asyncio
async def test_external_appium_is_checked_but_never_managed(tmp_path):
    spawned = False
    stopped = False

    async def spawn(_command: list[str], _log_path: Path):
        nonlocal spawned
        spawned = True

    async def stop(_process):
        nonlocal stopped
        stopped = True

    manager = AppiumServiceManager(
        AppiumServiceConfig(server_url="http://127.0.0.1:4723/wd/hub"),
        health_probe=lambda _url, _timeout: True,
        process_factory=spawn,
        process_stopper=stop,
        log_dir=tmp_path,
    )

    handle = await manager.start()
    assert handle.url == "http://127.0.0.1:4723/wd/hub"
    assert handle.managed is False
    await manager.stop()
    assert spawned is False
    assert stopped is False


@pytest.mark.asyncio
async def test_managed_appium_releases_process_and_port(tmp_path):
    port_manager = FakePortManager()
    commands: list[list[str]] = []
    stopped: list[object] = []
    process = SimpleNamespace(returncode=None, stdout=None, stderr=None, pid=123)

    async def spawn(command: list[str], log_path: Path):
        commands.append(command)
        assert log_path.parent == tmp_path
        return process

    async def stop(value):
        stopped.append(value)
        value.returncode = 0

    manager = AppiumServiceManager(
        AppiumServiceConfig(startup_timeout=0.1, poll_interval=0),
        port_manager=port_manager,
        health_probe=lambda _url, _timeout: True,
        process_factory=spawn,
        process_stopper=stop,
        log_dir=tmp_path,
    )

    handle = await manager.start()
    assert handle.url == "http://127.0.0.1:4723"
    assert handle.managed is True
    assert commands == [["appium", "--address", "127.0.0.1", "--port", "4723", "--base-path", "/"]]

    await manager.stop()
    assert stopped == [process]
    assert port_manager.lease.released is True
    await manager.stop()


@pytest.mark.asyncio
async def test_failed_managed_start_cleans_process_and_port(tmp_path):
    port_manager = FakePortManager()
    process = SimpleNamespace(returncode=None, stdout=None, stderr=None, pid=123)
    stopped: list[object] = []

    async def spawn(_command: list[str], _log_path: Path):
        return process

    async def stop(value):
        stopped.append(value)
        value.returncode = 1

    manager = AppiumServiceManager(
        AppiumServiceConfig(startup_timeout=0, poll_interval=0),
        port_manager=port_manager,
        health_probe=lambda _url, _timeout: False,
        process_factory=spawn,
        process_stopper=stop,
        log_dir=tmp_path,
    )

    with pytest.raises(AppiumServiceStartError, match="did not become ready"):
        await manager.start()

    assert stopped == [process]
    assert port_manager.lease.released is True
