# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Device Driver Factory & Registry."""

import os
from typing import TYPE_CHECKING

from adbutils import AdbClient

from artemis.config import settings
from artemis.context import DevicePlatform
from artemis.drivers.android.adb_driver import AndroidAdbDriver
from artemis.drivers.base import BaseDeviceDriver
from artemis.drivers.ios.device_hub_driver import IosDeviceHubDriver
from artemis.drivers.ios.xcuitest_driver import IosXcuiTestDriver
from artemis.drivers.mock.mock_driver import MockDeviceDriver
from artemis.utils.logger import get_logger

if TYPE_CHECKING:
    from artemis.context import ArtemisContext

logger = get_logger(__name__)


class UnsupportedPlatformDriverError(RuntimeError):
    """Raised when a platform is recognized but its dedicated driver is unavailable."""


def create_driver(ctx: "ArtemisContext") -> BaseDeviceDriver:
    """Instantiates the appropriate BaseDeviceDriver based on the runtime context."""
    # 1. Cloud mode check. Cloud devices are reached through the gateway's
    # RemoteUIAutomatorClient; ARTEMIS_HIERARCHY_BACKEND does not apply there
    # because the Accessibility Helper needs a local adb forward.
    if os.environ.get("ARTEMIS_CLOUD_MODE") == "1":
        if ctx.adb_client is None:
            from cloud_service.virtualization import RemoteAdbClient

            ctx.adb_client = RemoteAdbClient()
        if ctx.ui_adb_client is None:
            from cloud_service.virtualization import RemoteUIAutomatorClient

            ctx.ui_adb_client = RemoteUIAutomatorClient(adb_client=ctx.adb_client)

    # 2. Mock mode check
    if (
        getattr(ctx.device, "mobile_platform", None) == "mock"
        or os.environ.get("ARTEMIS_MOCK_DRIVER") == "1"
    ):
        return MockDeviceDriver(
            device_id=ctx.device.device_id if ctx.device else "mock-device",
            width=ctx.device.device_width if ctx.device else 1080,
            height=ctx.device.device_height if ctx.device else 2400,
        )

    # 3. A recognized platform must never silently fall back to another driver.
    if getattr(ctx.device, "mobile_platform", None) == DevicePlatform.IOS:
        from artemis.runtime import DeviceKind, device_registry

        kind = getattr(ctx.device, "device_kind", None)
        name = getattr(ctx.device, "device_name", None)
        if kind is None or (kind == DeviceKind.PHYSICAL and not name):
            descriptor = device_registry.select_device(DevicePlatform.IOS, ctx.device.device_id)
            kind = descriptor.kind
            name = descriptor.name
        physical_backend = os.environ.get("ARTEMIS_IOS_PHYSICAL_DRIVER", "device-hub").strip().lower()
        if kind == DeviceKind.PHYSICAL and physical_backend == "device-hub":
            if not name:
                raise UnsupportedPlatformDriverError("Device Hub requires the selected device name")
            return IosDeviceHubDriver(
                device_id=ctx.device.device_id,
                device_name=name,
                width=ctx.device.device_width,
                height=ctx.device.device_height,
            )
        if kind == DeviceKind.PHYSICAL and physical_backend != "appium":
            raise UnsupportedPlatformDriverError(
                "ARTEMIS_IOS_PHYSICAL_DRIVER must be 'device-hub' or 'appium'"
            )
        from artemis.runtime.appium_service import AppiumServiceConfig

        capabilities: dict[str, str | bool] = {}
        team_id = os.environ.get("ARTEMIS_IOS_XCODE_ORG_ID", "").strip()
        if team_id:
            capabilities["appium:xcodeOrgId"] = team_id
            capabilities["appium:xcodeSigningId"] = (
                os.environ.get("ARTEMIS_IOS_XCODE_SIGNING_ID", "").strip() or "Apple Development"
            )
        wda_bundle_id = os.environ.get("ARTEMIS_IOS_WDA_BUNDLE_ID", "").strip()
        if wda_bundle_id:
            capabilities["appium:updatedWDABundleId"] = wda_bundle_id
        if os.environ.get("ARTEMIS_IOS_SHOW_XCODE_LOG", "").lower() in {"1", "true", "yes"}:
            capabilities["appium:showXcodeLog"] = True

        return IosXcuiTestDriver(
            device_id=ctx.device.device_id,
            width=ctx.device.device_width,
            height=ctx.device.device_height,
            capabilities=capabilities,
            service_config=AppiumServiceConfig(
                server_url=os.environ.get("ARTEMIS_APPIUM_SERVER_URL") or None
            ),
        )

    # 4. Default Android ADB driver
    if ctx.adb_client is None:
        ctx.adb_client = AdbClient(
            host=settings.ADB_HOST or "localhost", port=settings.ADB_PORT or 5037
        )

    return AndroidAdbDriver(
        device_id=ctx.device.device_id,
        adb_client=ctx.adb_client,
        ui_adb_client=getattr(ctx, "ui_adb_client", None),
        width=ctx.device.device_width,
        height=ctx.device.device_height,
    )


def get_driver(ctx: "ArtemisContext") -> BaseDeviceDriver:
    """Cached accessor for device driver in the current context."""
    if not hasattr(ctx, "_active_driver") or getattr(ctx, "_active_driver") is None:
        driver = create_driver(ctx)
        setattr(ctx, "_active_driver", driver)
    return getattr(ctx, "_active_driver")
