from types import SimpleNamespace

import pytest

from artemis.context import ArtemisContext, DeviceContext, DevicePlatform
from artemis.agents.validator.tool_declarations import PRESS_KEY_TOOL
from artemis.mcp.action_executor import McpActionExecutor
from artemis.mcp.action_manifest import (
    DEVICE_ACTIONS,
    INTERNAL_ACTIONS,
    REQUIRED_ACTIONS,
    filter_declarations,
)
from artemis.mcp.action_specs import operator_shell_tool
from artemis.mcp.action_types import ActionCode
from artemis.mcp.actuators import IosActuator, create_actuator


class FakeDriver:
    def __init__(self):
        self.screen_size = (1179, 2556)
        self.calls: list[tuple] = []

    async def input_text(self, text, clear_existing=True):
        self.calls.append(("input_text", text, clear_existing))
        return True

    async def press_key(self, key):
        self.calls.append(("press_key", key))
        return True

    async def launch_app(self, bundle_id):
        self.calls.append(("launch_app", bundle_id))
        return True

    async def stop_app(self, bundle_id):
        self.calls.append(("stop_app", bundle_id))
        return True


class FakeController:
    def __init__(self):
        self.driver = FakeDriver()
        self.calls: list[tuple] = []

    async def tap_at(self, x, y, **kwargs):
        self.calls.append(("tap_at", x, y, kwargs))
        return SimpleNamespace(error=None)

    async def swipe_coords(self, x1, y1, x2, y2, duration):
        self.calls.append(("swipe_coords", x1, y1, x2, y2, duration))
        return None

    async def get_ui_elements(self):
        return []

    async def take_screenshot(self):
        return "image"

    async def get_screen_data(self):
        return SimpleNamespace(width=1179, height=2556)


@pytest.fixture
def ios_context():
    return ArtemisContext(
        device=DeviceContext(
            mobile_platform=DevicePlatform.IOS,
            device_id="SIM-UDID",
            device_width=1179,
            device_height=2556,
        )
    )


@pytest.fixture
def actuator(ios_context):
    return IosActuator(ios_context, FakeController())


def test_ios_capabilities_keep_required_and_internal_but_hide_open_link(actuator):
    assert REQUIRED_ACTIONS <= actuator.capabilities()
    assert INTERNAL_ACTIONS <= actuator.capabilities()
    assert actuator.capabilities() == DEVICE_ACTIONS - {"open_link"}
    assert actuator.constraints() == {"press_key": {"key": frozenset({"home", "enter", "delete"})}}


@pytest.mark.asyncio
async def test_click_uses_live_driver_screen_size(actuator):
    result = await actuator.click(500, 500)

    assert result.ok
    assert actuator.controller.calls[-1][:3] == ("tap_at", 589, 1278)


@pytest.mark.asyncio
async def test_input_text_clear_uses_xcuitest_semantics_without_shell(actuator):
    result = await actuator.input_text("hello", target=(500, 300), clear_exist=True)

    assert result.ok
    assert actuator.controller.driver.calls == [("input_text", "hello", True)]


@pytest.mark.asyncio
async def test_unsupported_ios_key_is_structured_and_not_dispatched(actuator):
    result = await actuator.press_key("BACK")

    assert not result.ok
    assert result.code is ActionCode.UNSUPPORTED
    assert actuator.controller.driver.calls == []


@pytest.mark.asyncio
async def test_ios_home_key_and_bundle_identifier_are_dispatched(actuator):
    key_result = await actuator.press_key("KEYCODE_HOME")
    launch_result = await actuator.manage_app("launch", "com.example.demo")
    stop_result = await actuator.manage_app("stop", "com.example.demo")

    assert key_result.ok and launch_result.ok and stop_result.ok
    assert actuator.controller.driver.calls == [
        ("press_key", "home"),
        ("launch_app", "com.example.demo"),
        ("stop_app", "com.example.demo"),
    ]


def test_platform_factory_selects_ios_actuator_and_caches_it(ios_context):
    controller = FakeController()

    first = create_actuator(ios_context, controller)
    second = create_actuator(ios_context, controller)

    assert isinstance(first, IosActuator)
    assert second is first
    assert ios_context.actuator is first


def test_executor_default_is_platform_aware(ios_context):
    executor = McpActionExecutor(ios_context, controller=FakeController())

    assert isinstance(executor.actuator, IosActuator)
    assert "open_link" not in executor.action_tool_names


def test_ios_key_constraint_is_projected_to_flash_and_operator_schemas(actuator):
    (flash_decl,) = filter_declarations([PRESS_KEY_TOOL], actuator, agent="flash")
    assert flash_decl.parameters["properties"]["key"]["enum"] == ["ENTER", "HOME"]

    operator_tool = operator_shell_tool("press_key", actuator.constraints()["press_key"])
    assert operator_tool.args_schema.model_json_schema()["properties"]["key"]["enum"] == [
        "ENTER",
        "HOME",
    ]
