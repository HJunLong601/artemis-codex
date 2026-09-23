"""Pure unit coverage for Device Hub safety and coordinate calibration."""

import cv2
import numpy as np
import pytest

from artemis.drivers.ios.device_hub_driver import (
    DeviceHubError,
    IosDeviceHubDriver,
    locate_screen,
)


def _png(image: np.ndarray) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def test_locate_phone_screen_after_device_hub_zoom():
    phone = np.full((850, 400), 235, dtype=np.uint8)
    for index in range(8):
        row = 60 + index * 88
        cv2.rectangle(phone, (30, row), (365, row + 45), (index * 23 + 20) % 255, -1)
        cv2.putText(phone, f"Screen {index}", (60, row + 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, 255, 2)
    scale = 0.5
    displayed = cv2.resize(phone, None, fx=scale, fy=scale)
    window = np.full((700, 1000), 26, dtype=np.uint8)
    window[120 : 120 + displayed.shape[0], 240 : 240 + displayed.shape[1]] = displayed

    x, y, width, height = locate_screen(_png(phone), _png(window))

    assert x == pytest.approx(240, abs=4)
    assert y == pytest.approx(120, abs=4)
    assert width == pytest.approx(200, abs=4)
    assert height == pytest.approx(425, abs=4)


def test_calibration_rejects_unrelated_window():
    phone = np.full((850, 400), 100, dtype=np.uint8)
    cv2.circle(phone, (200, 420), 120, 230, -1)
    window = np.full((700, 1000), 25, dtype=np.uint8)
    with pytest.raises(DeviceHubError, match="could not be matched"):
        locate_screen(_png(phone), _png(window))


def test_calibration_rejects_blank_phone_screen():
    phone = np.zeros((850, 400), dtype=np.uint8)
    window = np.zeros((700, 1000), dtype=np.uint8)
    with pytest.raises(DeviceHubError, match="lacks enough detail"):
        locate_screen(_png(phone), _png(window))


def test_physical_phone_point_maps_through_retina_window():
    window = {
        "x": 100.0, "y": 80.0, "width": 1000.0, "height": 700.0,
        "image_width": 2000, "image_height": 1400,
    }
    point = IosDeviceHubDriver._host_point(window, (480, 240, 400, 850), 200, 425, 400, 850)
    assert point == pytest.approx((440, 412.5))
    with pytest.raises(ValueError, match="outside"):
        IosDeviceHubDriver._host_point(window, (480, 240, 400, 850), 401, 425, 400, 850)
