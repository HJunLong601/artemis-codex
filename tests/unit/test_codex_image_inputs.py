"""Verify model-bound image bytes, provider handoff and source preservation."""

import base64
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import quote_from_bytes

import codex_client_provider.langchain as provider
from langchain_core.messages import HumanMessage
from PIL import Image
import pytest

from artemis.llm.codex_app_server import CodexAppServerChatModel
from artemis.llm.image_inputs import prepare_image_bytes, prepare_image_messages


@pytest.fixture(autouse=True)
def default_image_environment(monkeypatch):
    import os

    for name in os.environ:
        if name.startswith(("ARTEMIS_CODEX_IMAGE_", "CODEX_CLIENT_IMAGE_")):
            monkeypatch.delenv(name)


def encode(size=(1080, 2400), mode="RGB", color="navy", format="PNG", **kwargs):
    buffer = BytesIO()
    Image.new(mode, size, color).save(buffer, format=format, **kwargs)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "size,expected",
    [
        ((1080, 2400), (720, 1600)),
        ((1920, 1080), (1280, 720)),
        ((200, 100), (200, 100)),
        ((2000, 2000), (720, 720)),
    ],
)
def test_defaults_produce_bounded_lossy_webp_without_upscaling(size, expected):
    raw = encode(size)
    converted = prepare_image_bytes(raw)
    assert converted[12:16] == b"VP8 "  # A true lossy bitstream, not a renamed PNG.
    with Image.open(BytesIO(converted)) as image:
        assert image.format == "WEBP"
        assert image.size == expected
    assert prepare_image_bytes(converted) == converted  # No second lossy generation.
    assert provider._prepare_image_bytes(converted, ".webp") == (converted, ".webp", False)


def test_orientation_and_metadata_are_handled_before_resizing():
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private capture owner"
    converted = prepare_image_bytes(encode((1920, 1080), format="JPEG", exif=exif))
    with Image.open(BytesIO(converted)) as image:
        assert image.size == (720, 1280)
        assert not image.getexif()
        assert b"private capture owner" not in converted


def test_transparency_is_composited_on_white():
    converted = prepare_image_bytes(encode((100, 100), "RGBA", (10, 20, 30, 0)))
    with Image.open(BytesIO(converted)) as image:
        assert image.mode == "RGB"
        assert min(image.getpixel((50, 50))) >= 250


def test_byte_budget_reduces_dimensions_without_generic_jpeg_recompression(monkeypatch):
    monkeypatch.setenv("ARTEMIS_CODEX_IMAGE_WEBP_QUALITY", "100")
    monkeypatch.setenv("CODEX_CLIENT_IMAGE_MAX_BYTES", "65536")
    buffer = BytesIO()
    Image.effect_noise((1920, 1080), 128).convert("RGB").save(buffer, format="PNG")
    converted = prepare_image_bytes(buffer.getvalue())
    assert len(converted) <= 65536
    with Image.open(BytesIO(converted)) as image:
        assert image.height < 720
    assert provider._prepare_image_bytes(converted, ".webp")[2] is False


@pytest.mark.parametrize("edge", ["100", "invalid"])
def test_legacy_provider_limit_overrides_are_compatible(monkeypatch, edge):
    monkeypatch.setenv("CODEX_CLIENT_IMAGE_MAX_EDGE", edge)
    monkeypatch.setenv("ARTEMIS_CODEX_IMAGE_WEBP_QUALITY", "invalid")
    converted = prepare_image_bytes(encode())
    assert provider._prepare_image_bytes(converted, ".webp")[2] is False
    with Image.open(BytesIO(converted)) as image:
        assert max(image.size) == (320 if edge == "100" else 1600)


@pytest.mark.parametrize("form", ["nested", "flat", "input", "source", "percent", "local"])
def test_supported_image_blocks_preserve_history_and_original_files(form, tmp_path):
    raw = encode()
    url = "data:image/png;base64," + base64.b64encode(raw).decode()
    original_file = tmp_path / "capture.png"
    original_file.write_bytes(raw)
    if form == "nested":
        block = {"type": "image_url", "image_url": {"url": url, "detail": "high"}}
    elif form == "flat":
        block = {"type": "image_url", "image_url": url}
    elif form == "source":
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": url.split(",", 1)[1]},
        }
    else:
        value = (
            str(original_file)
            if form == "local"
            else ("data:image/png," + quote_from_bytes(raw) if form == "percent" else url)
        )
        block = {"type": "input_image", "url": value}
    message = HumanMessage(content=[{"type": "text", "text": "Read screen"}, block])
    snapshot = message.model_dump()
    prepared = prepare_image_messages([message])
    _, images, paths = provider._message_transcript(prepared)
    try:
        with Image.open(images[0]["path"]) as image:
            assert image.format == "WEBP"
            assert image.size == (720, 1600)
        assert message.model_dump() == snapshot
        assert original_file.read_bytes() == raw
        if form == "nested":
            assert prepared[0].content[1]["image_url"]["detail"] == "high"
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


def test_remote_url_remains_with_provider():
    message = HumanMessage(
        content=[
            {
                "type": "image_url",
                "image_url": {"url": "https://example.invalid/image.png", "detail": "high"},
            }
        ]
    )
    assert prepare_image_messages([message])[0].content == message.content


@pytest.mark.parametrize("telemetry", [True, False])
@pytest.mark.parametrize("fail", [True, False])
@pytest.mark.asyncio
async def test_actual_app_server_handoff_is_webp_and_temp_files_are_cleaned(
    monkeypatch, telemetry, fail
):
    paths = []

    async def complete(**kwargs):
        assert kwargs["model"] == "gpt-6.1-sol"
        assert kwargs["effort"] == "medium"
        for block in kwargs["inputs"]:
            if block["type"] == "localImage":
                path = Path(block["path"])
                paths.append(path)
                with Image.open(path) as image:
                    assert image.format == "WEBP"
                    assert image.size == (720, 1600)
        assert len(paths) == 1
        if fail:
            raise RuntimeError("model failure")
        return {"text": '{"content":"ready"}'}

    client = AsyncMock()
    client.run_completion.side_effect = complete
    monkeypatch.setattr(provider, "_client_for_running_loop", lambda **_: client)
    raw = encode()
    message = HumanMessage(
        content=[
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode()},
            }
        ]
    )
    model = CodexAppServerChatModel(
        model_name="gpt-6.1-sol", reasoning_effort="medium", telemetry_enabled=telemetry
    )
    if fail:
        with pytest.raises(RuntimeError, match="model failure"):
            await model.ainvoke([message])
    else:
        assert (await model.ainvoke([message])).content == "ready"
    assert all(not p.exists() for p in paths)
    client.run_completion.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_preprocessing_switch_preserves_benchmark_png(monkeypatch):
    monkeypatch.setenv("ARTEMIS_CODEX_IMAGE_PREPROCESSING", "0")
    monkeypatch.setenv("CODEX_CLIENT_IMAGE_MAX_EDGE", "2400")
    raw = encode()

    async def complete(**kwargs):
        image = next(b for b in kwargs["inputs"] if b["type"] == "localImage")
        assert Path(image["path"]).read_bytes() == raw
        return {"text": '{"content":"ready"}'}

    client = AsyncMock()
    client.run_completion.side_effect = complete
    monkeypatch.setattr(provider, "_client_for_running_loop", lambda **_: client)
    message = HumanMessage(
        content=[
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode()},
            }
        ]
    )
    model = CodexAppServerChatModel(model_name="test")
    assert not model.image_preprocessing_enabled
    await model.ainvoke([message])
