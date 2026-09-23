from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.context import ArtemisContext, DeviceContext, DevicePlatform
from artemis.controllers.platform_specific_commands_controller import (
    get_current_foreground_package_async,
)


@pytest.mark.asyncio
async def test_ios_foreground_package_uses_platform_driver():
    context = ArtemisContext(
        device=DeviceContext(
            mobile_platform=DevicePlatform.IOS,
            device_id="SIM-UDID",
        )
    )
    controller = MagicMock()
    controller.driver.get_current_package = AsyncMock(return_value="com.apple.Preferences")

    with patch(
        "artemis.controllers.controller_factory.get_controller",
        return_value=controller,
    ):
        package = await get_current_foreground_package_async(context)

    assert package == "com.apple.Preferences"
    controller.driver.get_current_package.assert_awaited_once_with()
