import base64
from pathlib import Path
import struct

import pytest

from artemis.drivers.base import UnsupportedOperationError
from artemis.drivers.ios.xcuitest_driver import IosXcuiTestDriver


WDA_XML = """<AppiumAUT>
  <XCUIElementTypeApplication type="XCUIElementTypeApplication" name="Settings"
      enabled="true" visible="true" x="0" y="0" width="393" height="852">
    <XCUIElementTypeButton type="XCUIElementTypeButton" name="General"
        label="General" enabled="true" visible="true"
        x="10" y="20" width="100" height="44" />
  </XCUIElementTypeApplication>
</AppiumAUT>"""


def _png_header(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height)


class FakeClient:
    def __init__(self):
        self.commands: list[tuple[str, str, object]] = []

    async def command(self, method: str, path: str, payload=None):
        self.commands.append((method, path, payload))
        if path == "/window/rect":
            return {"x": 0, "y": 0, "width": 393, "height": 852}
        if path == "/screenshot":
            return base64.b64encode(_png_header(1179, 2556)).decode()
        if path == "/source":
            return WDA_XML
        if path == "/actions":
            return None
        raise AssertionError(f"Unexpected command: {method} {path}")


class FakeSessionManager:
    def __init__(self):
        self.client = FakeClient()
        self.capabilities = None
        self.stopped = False

    async def start(self, capabilities):
        self.capabilities = capabilities
        return self.client

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_driver_captures_screen_and_converts_tap_coordinates():
    sessions = FakeSessionManager()
    driver = IosXcuiTestDriver("sim-1", session_manager=sessions, settle_delay=0)
    await driver.connect()

    screen = await driver.get_screen_data(skip_settling=True)
    assert screen.platform == "ios"
    assert screen.width == 1179
    assert screen.height == 2556
    assert any(element["resource_id"] == "General" for element in screen.ui_elements)

    assert await driver.tap(300, 600)
    _, path, payload = sessions.client.commands[-1]
    assert path == "/actions"
    move = payload["actions"][0]["actions"][0]
    assert move["x"] == 100
    assert move["y"] == 200

    await driver.disconnect()
    assert sessions.stopped is True


@pytest.mark.asyncio
async def test_driver_rejects_ios_shell():
    driver = IosXcuiTestDriver("sim-1", session_manager=FakeSessionManager())

    with pytest.raises(UnsupportedOperationError, match="shell"):
        await driver.execute_shell("ls")


@pytest.mark.asyncio
async def test_simulator_recording_uses_simctl(monkeypatch, tmp_path):
    class FakeProcess:
        returncode = None

        def __init__(self):
            self.output = None

        async def wait(self):
            if self.returncode is None:
                await asyncio.sleep(60)
            return self.returncode

        def send_signal(self, _signal):
            self.returncode = 0
            self.output.write_bytes(b"mp4")

        def kill(self):
            self.returncode = -9

    import asyncio

    process = FakeProcess()
    expected_output = tmp_path / "ios-sim-1-recording.mp4"
    expected_output.write_bytes(b"stale recording")

    async def spawn(*args, **_kwargs):
        process.output = Path(args[-1])
        assert process.output == expected_output
        assert not process.output.exists()
        return process

    monkeypatch.setattr("asyncio.create_subprocess_exec", spawn)
    driver = IosXcuiTestDriver("sim-1", session_manager=FakeSessionManager())

    await driver.start_video_recording(tmp_path)
    output = await driver.stop_video_recording()

    assert output == str(expected_output)
    assert Path(output).read_bytes() == b"mp4"
