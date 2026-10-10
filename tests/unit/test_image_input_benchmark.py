"""Guard against falsely reporting accurate image benchmark samples."""

import base64
import json
import io
import os

import pytest

from scripts.benchmark_image_inputs import VARIANTS, messages_for, score
from scripts.benchmark_png_webp import convert, native_limits, provider
from PIL import Image


@pytest.fixture
def observed_case():
    # ARTEMIS trace 3cfb8c37: actual 1080x2400 Settings screen regions.
    return {
        "width": 1080,
        "height": 2400,
        "ocr": [
            {"text": "蓝牙", "bbox": [203, 331, 303, 398]},
            {"text": "个人热点", "bbox": [203, 492, 404, 559]},
            {"text": "连接与共享", "bbox": [203, 811, 454, 878]},
        ],
        "target": {"text": "显示", "bbox": [203, 1414, 303, 1481]},
    }


def test_verified_observation_scores_in_original_screen_coordinates(observed_case):
    result = score(
        observed_case, {"texts": ["蓝牙", "个人热点", "连接与共享"], "point": [235, 603]}
    )
    assert result["passed"]


@pytest.mark.parametrize("point", [[-1, -1], [603, 235], [235, 1001], [True, 603], None])
def test_correct_text_does_not_hide_invalid_or_mislocated_point(observed_case, point):
    result = score(observed_case, {"texts": ["蓝牙", "个人热点", "连接与共享"], "point": point})
    assert result["ocr_correct"] == 3
    assert not result["target_hit"]
    assert not result["passed"]


def test_ocr_order_and_partial_transcription_are_not_accepted(observed_case):
    result = score(observed_case, {"texts": ["个人热点", "蓝牙", "连接"], "point": [235, 603]})
    assert result["target_hit"]
    assert result["ocr_correct"] == 0
    assert not result["passed"]


def test_prompt_does_not_leak_ground_truth(observed_case):
    messages = messages_for(observed_case, b"image bytes", VARIANTS[0])
    query = json.loads(messages[1].content[0]["text"])
    assert set(query) == {"read_regions_ltrb", "locate"}
    assert query["locate"] == "显示"
    assert all(o["text"] not in str(messages) for o in observed_case["ocr"])
    url = messages[1].content[1]["image_url"]["url"]
    assert base64.b64decode(url.split(",")[1]) == b"image bytes"


def test_native_comparison_preserves_both_formats_at_provider_boundary():
    source = io.BytesIO()
    Image.new("RGB", (1080, 2400), "white").save(source, format="PNG")
    png = source.getvalue()
    webp = convert(png)
    with Image.open(io.BytesIO(webp)) as decoded:
        assert decoded.format == "WEBP"
        assert decoded.size == (1080, 2400)
    with native_limits(2400, 16 * 1024 * 1024):
        for raw, suffix in [(png, ".png"), (webp, ".webp")]:
            prepared, actual_suffix, changed = provider._prepare_image_bytes(raw, suffix)
            assert prepared == raw
            assert actual_suffix == suffix
            assert not changed


def test_native_overrides_restore_environment_even_after_failure(monkeypatch):
    monkeypatch.setenv("CODEX_CLIENT_IMAGE_MAX_EDGE", "1600")
    monkeypatch.delenv("CODEX_CLIENT_IMAGE_MAX_BYTES", raising=False)
    with pytest.raises(RuntimeError), native_limits(2400, 16 * 1024 * 1024):
        assert os.environ["CODEX_CLIENT_IMAGE_MAX_EDGE"] == "2400"
        raise RuntimeError("interrupted benchmark")
    assert os.environ["CODEX_CLIENT_IMAGE_MAX_EDGE"] == "1600"
    assert "CODEX_CLIENT_IMAGE_MAX_BYTES" not in os.environ
