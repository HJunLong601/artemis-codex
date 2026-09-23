from artemis.drivers.ios.coordinate_space import CoordinateSpace
from artemis.drivers.ios.wda_xml import parse_wda_xml


WDA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<AppiumAUT>
  <XCUIElementTypeApplication type="XCUIElementTypeApplication" name="Settings"
      enabled="true" visible="true" x="0" y="0" width="393" height="852">
    <XCUIElementTypeButton type="XCUIElementTypeButton" name="General"
        label="General" enabled="true" visible="true"
        x="10" y="20" width="100" height="44" />
  </XCUIElementTypeApplication>
</AppiumAUT>
"""


def test_wda_xml_maps_semantics_and_physical_bounds():
    space = CoordinateSpace(
        screenshot_width=1179,
        screenshot_height=2556,
        viewport_width=393,
        viewport_height=852,
    )

    elements = parse_wda_xml(WDA_XML, space)
    button = next(element for element in elements if element["resource_id"] == "General")

    assert button["text"] == "General"
    assert button["content-desc"] == "General"
    assert button["class"] == "XCUIElementTypeButton"
    assert button["clickable"] is True
    assert button["bounds"] == "[30,60][330,192]"
    assert button["parsed_bounds"] == {
        "left": 30,
        "top": 60,
        "right": 330,
        "bottom": 192,
    }
