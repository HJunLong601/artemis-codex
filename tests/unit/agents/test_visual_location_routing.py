"""XML first, then persistent locations, then visual inference; never cached actions."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from artemis.agents.explorer.explorer import Explorer
from artemis.graph.state import State
import artemis.agents.object_detector.object_detector as detector


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTEMIS_VISUAL_LOCATION_CACHE", "1")
    monkeypatch.setenv("ARTEMIS_VISUAL_LOCATION_CACHE_PATH", str(tmp_path / "locations.sqlite3"))
    path = tmp_path / "screen.png"
    Image.new("RGB", (100, 200), "white").save(path)
    state = State.initial("Open the verified settings screen")
    state.latest_screenshot = str(path)
    state.latest_ui_hierarchy = [{"text": "Settings", "bounds": "[10,20][40,60]"}]
    ctx = SimpleNamespace(
        device=SimpleNamespace(
            mobile_platform="android",
            device_id="phone-a",
            device_width=100,
            device_height=200,
            device_name="test phone",
        ),
        llm_config=SimpleNamespace(explorer=SimpleNamespace(model="gpt-6.1-sol")),
        agent_config=None,
        execution_setup=None,
        data_engine=None,
    )
    driver = SimpleNamespace(get_current_package=AsyncMock(return_value="com.android.settings"))
    monkeypatch.setattr("artemis.drivers.factory.get_driver", lambda _: driver)
    monkeypatch.setattr(Explorer, "_resolve_image_record", lambda *_: (None, None))
    monkeypatch.setattr(detector, "get_llm", lambda *_, **__: object())

    async def detected(*args, label, **kwargs):
        return [{"label": label, "point": [700, 300]}]  # Model uses y,x; cache must preserve x,y.

    inference = AsyncMock(side_effect=detected)
    monkeypatch.setattr(detector, "_detect_single_label", inference)
    return ctx, state, driver, inference


async def run(ctx, state, query="gear icon", feedback="", version="flash"):
    return json.loads(
        await Explorer(ctx).run(query, feedback, state.latest_screenshot, state, version=version)
    )


@pytest.mark.asyncio
async def test_xml_first_never_queries_the_cache_device_or_visual_model(environment):
    ctx, state, driver, inference = environment
    result = await run(ctx, state, query="Settings")
    assert result["candidates"][0]["source"] == "xml"
    driver.get_current_package.assert_not_awaited()
    inference.assert_not_awaited()


@pytest.mark.asyncio
async def test_flash_reuses_positions_across_new_explorers_without_another_model_call(environment):
    ctx, state, _, inference = environment
    first = await run(ctx, state)
    second = await run(ctx, state)
    assert first["candidates"][0]["coords"] == [300, 700]
    assert second["candidates"] == first["candidates"]
    assert second["location_cache_hits"] == 1
    inference.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_xml_and_multi_target_cache_hits_skip_only_resolved_targets(environment):
    ctx, state, _, inference = environment
    await run(ctx, state, query="gear icon | blue icon")
    assert inference.await_count == 2
    result = await run(ctx, state, query="Settings | gear icon | blue icon | red icon")
    assert result["location_cache_hits"] == 2
    assert len(result["candidates"]) == 4
    assert inference.await_count == 3  # Only red icon reached visual inference.


@pytest.mark.asyncio
async def test_tree_pixels_application_and_device_changes_each_require_new_inference(environment):
    ctx, state, driver, inference = environment
    await run(ctx, state)
    state.latest_ui_hierarchy[0]["text"] = "Changed settings"
    await run(ctx, state)
    image = Image.open(state.latest_screenshot).convert("RGB")
    image.putpixel((50, 100), (254, 255, 255))
    image.save(state.latest_screenshot)
    await run(ctx, state)
    driver.get_current_package.return_value = "test.application"
    await run(ctx, state)
    ctx.device.device_id = "phone-b"
    await run(ctx, state)
    assert inference.await_count == 5


@pytest.mark.asyncio
async def test_correction_bypasses_and_invalidates_previously_cached_location(environment):
    ctx, state, _, inference = environment
    await run(ctx, state)
    await run(ctx, state, feedback="Wrong button; locate again")
    await run(ctx, state)
    assert inference.await_count == 3


@pytest.mark.asyncio
async def test_failed_execution_incident_bypasses_cache_until_resolved(environment):
    ctx, state, _, inference = environment
    await run(ctx, state)
    state.open_incident = {"category": "execution_failed"}
    await run(ctx, state)
    state.open_incident = None
    await run(ctx, state)
    assert inference.await_count == 3


@pytest.mark.asyncio
async def test_historical_screenshot_never_borrows_current_device_cache(environment, tmp_path):
    ctx, state, _, inference = environment
    old = str(tmp_path / "old.png")
    Image.new("RGB", (100, 200), "white").save(old)
    await Explorer(ctx).run("gear icon", "", old, state, version="flash")
    await Explorer(ctx).run("gear icon", "", old, state, version="flash")
    assert inference.await_count == 2


@pytest.mark.asyncio
async def test_disabled_unknown_device_and_unavailable_package_each_bypass_cache(
    environment, monkeypatch
):
    ctx, state, driver, inference = environment
    monkeypatch.setenv("ARTEMIS_VISUAL_LOCATION_CACHE", "0")
    await run(ctx, state)
    await run(ctx, state)
    monkeypatch.setenv("ARTEMIS_VISUAL_LOCATION_CACHE", "1")
    ctx.device.device_id = "default-device"
    await run(ctx, state)
    await run(ctx, state)
    ctx.device.device_id = "phone-a"
    driver.get_current_package.return_value = None
    await run(ctx, state)
    await run(ctx, state)
    assert inference.await_count == 6


@pytest.mark.asyncio
async def test_pro_hit_skips_both_image_ocr_and_the_reasoning_loop(environment, monkeypatch):
    ctx, state, _, _ = environment
    engine = AsyncMock(
        return_value=json.dumps(
            {"candidates": [{"coords": [300, 700], "description": "gear"}], "fallback_message": ""}
        )
    )
    ocr = AsyncMock(return_value=state.latest_ui_hierarchy)
    monkeypatch.setattr(Explorer, "_run_loop", engine)
    monkeypatch.setattr(Explorer, "_load_ocr_screen", ocr)
    await run(ctx, state, version="pro")
    second = await run(ctx, state, version="pro")
    assert second["location_cache_hits"] == 1
    engine.assert_awaited_once()
    ocr.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidates", [[], [{"coords": [-1, -1]}], [{"coords": [300, 700]}, {"coords": [400, 700]}]]
)
async def test_missing_invalid_and_ambiguous_answers_never_enter_cache(
    environment, monkeypatch, candidates
):
    ctx, state, _, _ = environment
    engine = AsyncMock(return_value=json.dumps({"candidates": candidates, "fallback_message": ""}))
    monkeypatch.setattr(Explorer, "_run_loop", engine)
    await run(ctx, state, version="pro")
    await run(ctx, state, version="pro")
    assert engine.await_count == 2


@pytest.mark.asyncio
async def test_direct_detector_tool_retains_xml_before_cache_or_model(environment):
    ctx, state, driver, inference = environment
    answer = await detector._run_object_detection(
        ctx, state.latest_screenshot, ["Settings"], state=state
    )
    assert answer["detected"][0]["source"] == "xml"
    driver.get_current_package.assert_not_awaited()
    inference.assert_not_awaited()
