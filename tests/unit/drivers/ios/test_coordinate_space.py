import pytest

from artemis.drivers.ios.coordinate_space import CoordinateSpace, CoordinateSpaceError


def test_coordinate_space_converts_between_screenshot_pixels_and_logical_points():
    space = CoordinateSpace(
        screenshot_width=1179,
        screenshot_height=2556,
        viewport_width=393,
        viewport_height=852,
    )

    assert space.scale_x == 3
    assert space.scale_y == 3
    assert space.physical_to_logical(300, 600) == (100, 200)
    assert space.logical_to_physical(10, 20) == (30, 60)
    assert space.logical_rect_to_physical(10, 20, 100, 44) == {
        "left": 30,
        "top": 60,
        "right": 330,
        "bottom": 192,
    }


def test_coordinate_space_rejects_missing_dimensions():
    with pytest.raises(CoordinateSpaceError, match="positive"):
        CoordinateSpace(
            screenshot_width=0,
            screenshot_height=2556,
            viewport_width=393,
            viewport_height=852,
        )
