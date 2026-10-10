"""Verify resized benchmark payloads survive the actual default adapter."""

import io

from PIL import Image
import pytest

from scripts.benchmark_webp_720p import convert, provider


@pytest.mark.parametrize("size, expected", [((1080, 2400), (720, 1600)), ((360, 800), (360, 800))])
def test_720_conversion_preserves_aspect_ratio_and_survives_default_adapter(
    monkeypatch, size, expected
):
    for name in [
        "CODEX_CLIENT_IMAGE_MAX_EDGE",
        "CODEX_CLIENT_IMAGE_MAX_BYTES",
        "ARTEMIS_CODEX_IMAGE_MAX_EDGE",
        "ARTEMIS_CODEX_IMAGE_MAX_BYTES",
    ]:
        monkeypatch.delenv(name, raising=False)
    source = io.BytesIO()
    Image.new("RGB", size, "white").save(source, format="PNG")
    raw = convert(source.getvalue(), 720)
    with Image.open(io.BytesIO(raw)) as decoded:
        assert decoded.size == expected
        assert decoded.format == "WEBP"
    prepared, suffix, changed = provider._prepare_image_bytes(raw, ".webp")
    assert prepared == raw and suffix == ".webp" and not changed
