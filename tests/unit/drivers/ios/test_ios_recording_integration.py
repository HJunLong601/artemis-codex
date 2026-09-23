from pathlib import Path

import pytest

from artemis.context import ArtemisContext, DeviceContext, DevicePlatform
from artemis.controllers.unified_controller import UnifiedMobileController
from artemis.utils.video import get_active_session, remove_active_session


class RecordingDriver:
    device_id = "SIM-UDID"

    def __init__(self):
        self.started_with: Path | None = None
        self.stopped = False

    async def start_video_recording(self, output_dir=None):
        self.started_with = Path(output_dir)

    async def stop_video_recording(self):
        self.stopped = True
        path = self.started_with / "ios-SIM-UDID-recording.mp4"
        path.write_bytes(b"mp4")
        return str(path)


@pytest.mark.asyncio
async def test_ios_controller_recording_uses_driver_and_tracks_session(tmp_path):
    driver = RecordingDriver()
    ctx = ArtemisContext(
        device=DeviceContext(
            mobile_platform=DevicePlatform.IOS,
            device_id=driver.device_id,
        )
    )
    ctx._active_driver = driver
    controller = UnifiedMobileController(ctx)
    remove_active_session(driver.device_id)

    started = await controller.start_video_recording(output_dir=tmp_path)

    assert started.success
    assert driver.started_with == tmp_path
    assert get_active_session(driver.device_id) is not None

    stopped = await controller.stop_video_recording()

    assert stopped.success
    assert stopped.video_path == tmp_path / "ios-SIM-UDID-recording.mp4"
    assert driver.stopped
    assert get_active_session(driver.device_id) is None
