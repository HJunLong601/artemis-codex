import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_server.background.task_runner import _initialize_agent


@pytest.mark.asyncio
async def test_background_agent_initialization_has_hard_timeout():
    async def slow_init(**_):
        await asyncio.sleep(60)

    agent = MagicMock()
    agent.init = AsyncMock(side_effect=slow_init)

    with pytest.raises(TimeoutError, match="initialization exceeded 0.0s"):
        await _initialize_agent(
            agent,
            retry_count=1,
            retry_wait_seconds=1,
            timeout_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_background_agent_initialization_forwards_health_settings():
    agent = AsyncMock()

    await _initialize_agent(
        agent,
        retry_count=3,
        retry_wait_seconds=4,
        timeout_seconds=1.0,
    )

    agent.init.assert_awaited_once_with(retry_count=3, retry_wait_seconds=4)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("knobs", "expect_level", "expect_mode"),
    [
        ({"verification_level": "strict", "explorer_pro_mode": "ultra"}, "strict", "ultra"),
        ({}, None, None),
    ],
)
async def test_run_task_applies_pro_tuning_to_agent_config(
    tmp_path, monkeypatch, knobs, expect_level, expect_mode
):
    """The detached runner applies --verification-level / --explorer-pro-mode on the builder."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from artemis.runtime import trace_store
    from mcp_server.background import task_runner as bg

    monkeypatch.setattr(trace_store, "TRACES_DIR", str(tmp_path))
    trace_id = "trace-tuning"
    trace_store.init_trace(
        trace_id=trace_id,
        task_desc="Audit checkout",
        model="Pro",
        conversation_id="",
        device_serial="emulator-5554",
    )

    fake_builder = MagicMock()
    fake_builders = MagicMock()
    fake_builders.AgentConfig.with_default_profile.return_value = fake_builder
    fake_agent = MagicMock()
    fake_agent._device_context = SimpleNamespace(device_id="emulator-5554")
    fake_agent.run_task = AsyncMock(return_value="done")
    fake_agent.clean = AsyncMock()

    monkeypatch.setattr(bg.device_utils, "resolve_adb_path", lambda: "adb")
    monkeypatch.setattr(bg.device_utils, "get_connected_devices", lambda _adb: ["emulator-5554"])
    monkeypatch.setattr(bg, "resolve_profile_file", lambda: None)
    monkeypatch.setattr(bg, "_initialize_agent", AsyncMock())
    monkeypatch.setattr(bg, "notify", MagicMock())

    with (
        patch("artemis.sdk.builders.Builders", fake_builders),
        patch("artemis.sdk.Agent", return_value=fake_agent),
        patch("artemis.sdk.types.AgentProfile", MagicMock()),
        patch("artemis.config.initialize_llm_config", return_value=MagicMock()),
    ):
        await bg.run_task(
            trace_id=trace_id,
            task_desc="Audit checkout",
            model="Pro",
            conversation_id="",
            device_serial="emulator-5554",
            **knobs,
        )

    if expect_level is None:
        fake_builder.with_verification_level.assert_not_called()
        fake_builder.with_explorer.assert_not_called()
    else:
        fake_builder.with_verification_level.assert_called_once_with(expect_level)
        fake_builder.with_explorer.assert_called_once_with(pro_mode=expect_mode)
    fake_agent.run_task.assert_awaited_once()
    assert trace_store.read_status(trace_id)["status"] == "completed"


@pytest.mark.asyncio
async def test_run_task_configures_ios_without_adb_discovery(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import patch

    from artemis.context import DevicePlatform
    from artemis.runtime import trace_store
    from mcp_server.background import task_runner as bg

    monkeypatch.setattr(trace_store, "TRACES_DIR", str(tmp_path))
    trace_store.init_trace(
        "trace-ios",
        "Open iOS Settings",
        "Flash",
        device_serial="SIM-UDID",
        device_platform="ios",
    )

    fake_builder = MagicMock()
    fake_builders = MagicMock()
    fake_builders.AgentConfig.with_default_profile.return_value = fake_builder
    fake_agent = MagicMock()
    fake_agent._device_context = SimpleNamespace(
        device_id="SIM-UDID", mobile_platform=DevicePlatform.IOS
    )
    fake_agent.run_task = AsyncMock(return_value="done")
    fake_agent.clean = AsyncMock()

    get_devices = MagicMock(side_effect=AssertionError("ADB discovery must not run for iOS"))
    monkeypatch.setattr(bg.device_utils, "get_connected_devices", get_devices)
    monkeypatch.setattr(bg, "resolve_profile_file", lambda: None)
    monkeypatch.setattr(bg, "_initialize_agent", AsyncMock())
    monkeypatch.setattr(bg, "notify", MagicMock())

    with (
        patch("artemis.sdk.builders.Builders", fake_builders),
        patch("artemis.sdk.Agent", return_value=fake_agent),
        patch("artemis.sdk.types.AgentProfile", MagicMock()),
        patch("artemis.config.initialize_llm_config", return_value=MagicMock()),
    ):
        await bg.run_task(
            trace_id="trace-ios",
            task_desc="Open iOS Settings",
            model="Flash",
            conversation_id="",
            device_serial="SIM-UDID",
            device_platform="ios",
        )

    get_devices.assert_not_called()
    fake_builder.for_device.assert_called_once_with(DevicePlatform.IOS, "SIM-UDID")
    status = trace_store.read_status("trace-ios")
    assert status["status"] == "completed"
    assert status["device_platform"] == "ios"
