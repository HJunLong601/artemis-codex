"""Artemis image policy at the Codex model boundary, independent of capture."""

from __future__ import annotations

import base64
from io import BytesIO
import os
from pathlib import Path
from urllib.parse import unquote_to_bytes

from langchain_core.messages import SystemMessage
from PIL import Image, ImageOps


def _limit(name, default, minimum, maximum=None, *, provider_override=False):
    value = os.getenv(f"ARTEMIS_CODEX_IMAGE_{name}", str(default))
    if provider_override:
        value = os.getenv(f"CODEX_CLIENT_IMAGE_{name}", value)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if provider_override:
        # Match the generic provider's lower bounds so it cannot recompress.
        return max(minimum, number)
    if number < minimum or (maximum is not None and number > maximum):
        return default
    return number


def prepare_image_bytes(raw: bytes) -> bytes:
    """Return an oriented, proportionally resized, metadata-free lossy WebP.

    Default bounds: short edge 720, long edge 1600, 768 KiB, quality 70/100.
    Never upscale. Already bounded WebP passes through to avoid generation loss.
    Keep quality fixed when further resizing is needed for the byte budget.
    """
    short_edge = _limit("SHORT_EDGE", 720, 1)
    max_edge = _limit("MAX_EDGE", 1600, 320, provider_override=True)
    max_bytes = _limit("MAX_BYTES", 768 * 1024, 64 * 1024, provider_override=True)
    quality = _limit("WEBP_QUALITY", 70, 0, 100)
    with Image.open(BytesIO(raw)) as source:
        source.seek(0)
        image = ImageOps.exif_transpose(source)
        width, height = image.size
        scale = min(1.0, short_edge / min(width, height), max_edge / max(width, height))
        if (
            source.format == "WEBP"
            and raw[12:16] == b"VP8 "
            and not source.is_animated
            and scale == 1.0
            and len(raw) <= max_bytes
            and not source.info.get("exif")
            and not source.info.get("icc_profile")
            and not source.info.get("xmp")
        ):
            return raw
        image = image.copy()
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", image.size, "white")
        image = Image.alpha_composite(background, rgba).convert("RGB")
    else:
        image = image.convert("RGB")
    if scale < 1:
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
    # Do not forward capture EXIF, ICC or other metadata to the model.
    image.info.clear()
    while True:
        buffer = BytesIO()
        image.save(buffer, format="WEBP", quality=quality, lossless=False, method=4)
        encoded = buffer.getvalue()
        if len(encoded) <= max_bytes:
            return encoded
        if image.size == (1, 1):
            raise ValueError("WebP image could not fit the configured byte budget")
        image = image.resize(
            tuple(max(1, int(edge * 0.85)) for edge in image.size),
            Image.Resampling.LANCZOS,
        )


def _prepare_url(url: str) -> str:
    if url.lower().startswith("data:image/"):
        header, separator, payload = url.partition(",")
        if not separator:
            raise ValueError("Invalid image data URL")
        raw = (
            base64.b64decode(payload) if ";base64" in header.lower() else unquote_to_bytes(payload)
        )
    elif "://" not in url and Path(url).is_file():
        raw = Path(url).read_bytes()
    else:
        # Remote URLs are left to the provider; do not fetch user content here.
        return url
    prepared = prepare_image_bytes(raw)
    return "data:image/webp;base64," + base64.b64encode(prepared).decode("ascii")


def prepare_image_messages(messages):
    """Copy image blocks without mutating caller messages, history or disk files."""
    prepared = []
    for message in messages:
        if isinstance(message, SystemMessage) or not isinstance(message.content, list):
            prepared.append(message)
            continue
        blocks = []
        for block in message.content:
            if not isinstance(block, dict) or block.get("type") not in {
                "image_url",
                "input_image",
                "image",
            }:
                blocks.append(block)
                continue
            url = block.get("image_url") or block.get("url")
            nested = url if isinstance(url, dict) else None
            if nested is not None:
                url = nested.get("url")
            source = block.get("source")
            if not url and isinstance(source, dict) and source.get("data"):
                url = f"data:{source.get('media_type') or 'image/png'};base64,{source['data']}"
            if not url:
                blocks.append(block)
                continue
            updated = _prepare_url(str(url))
            if updated == url:
                blocks.append(block)
                continue
            new_block = dict(block)
            if block.get("image_url"):
                new_block["image_url"] = {**nested, "url": updated} if nested else updated
            elif block.get("url"):
                new_block["url"] = updated
            else:
                new_block["source"] = {
                    **source,
                    "media_type": "image/webp",
                    "data": updated.split(",", 1)[1],
                }
            blocks.append(new_block)
        prepared.append(message.model_copy(update={"content": blocks}))
    return prepared
