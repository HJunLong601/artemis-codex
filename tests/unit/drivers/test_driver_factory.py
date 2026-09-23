from unittest.mock import Mock, patch

import pytest

from artemis.context import ArtemisContext, DeviceContext, DevicePlatform
from artemis.drivers.factory import create_driver
from artemis.drivers.ios.device_hub_driver import IosDeviceHubDriver
from artemis.drivers.ios.xcuitest_driver import IosXcuiTestDriver


def test_ios_context_creates_ios_driver_without_touching_adb():
    context = ArtemisContext(
        device=DeviceContext(
            mobile_platform=DevicePlatform.IOS,
            device_id="ios-sim-1",
            device_kind="simulator",
        )
    )

    with patch("artemis.drivers.factory.AdbClient", Mock()) as adb_client:
        driver = create_driver(context)

    assert isinstance(driver, IosXcuiTestDriver)
    adb_client.assert_not_called()


def test_ios_physical_device_defaults_to_device_hub_without_touching_adb(monkeypatch):
    context = ArtemisContext(
        device=DeviceContext(
            mobile_platform=DevicePlatform.IOS,
            device_id="REAL-UDID",
            device_kind="physical",
            device_name="Test iPhone",
        )
    )
    monkeypatch.delenv("ARTEMIS_IOS_PHYSICAL_DRIVER", raising=False)
    with patch("artemis.drivers.factory.AdbClient", Mock()) as adb_client:
        driver = create_driver(context)
    assert isinstance(driver, IosDeviceHubDriver)
    adb_client.assert_not_called()


def test_ios_physical_appium_fallback_signing_capabilities_are_opt_in(monkeypatch):
    context = ArtemisContext(
        device=DeviceContext(
            mobile_platform=DevicePlatform.IOS,
            device_id="REAL-UDID",
            device_kind="physical",
            device_name="Test iPhone",
        )
    )
    monkeypatch.setenv("ARTEMIS_IOS_PHYSICAL_DRIVER", "appium")
    monkeypatch.setenv("ARTEMIS_IOS_XCODE_ORG_ID", "TESTTEAM123")
    monkeypatch.setenv("ARTEMIS_IOS_WDA_BUNDLE_ID", "org.example.wda")
    monkeypatch.setenv("ARTEMIS_IOS_SHOW_XCODE_LOG", "true")

    driver = create_driver(context)
    capabilities = driver._capabilities()

    assert capabilities["appium:xcodeOrgId"] == "TESTTEAM123"
    assert capabilities["appium:xcodeSigningId"] == "Apple Development"
    assert capabilities["appium:updatedWDABundleId"] == "org.example.wda"
    assert capabilities["appium:showXcodeLog"] is True
