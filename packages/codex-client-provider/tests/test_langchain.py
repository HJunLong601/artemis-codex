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

"""Unit coverage for the key-free Codex App Server LangChain adapter."""

import base64
from io import BytesIO
import json
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from PIL import Image
from pydantic import BaseModel
import pytest

import codex_client_provider.langchain as codex_module
from codex_client_provider import CodexAppServerChatModel


class FakeClient:
    def __init__(self, text: str):
        self.text = text
        self.calls = []
        self.local_image_bytes = []

    async def run_completion(self, **kwargs):
        self.calls.append(kwargs)
        for item in kwargs.get("inputs", []):
            if item.get("type") == "localImage":
                self.local_image_bytes.append(Path(item["path"]).read_bytes())
        return {
            "text": self.text,
            "thread_id": "thread-test",
            "model": "gpt-test",
            "usage": {"last": {"inputTokens": 12, "outputTokens": 4, "totalTokens": 16}},
        }


@pytest.mark.asyncio
async def test_plain_completion_uses_app_server_contract(monkeypatch):
    fake = FakeClient(json.dumps({"content": "ready"}))
    monkeypatch.setattr(codex_module, "_client_for_running_loop", lambda **_kwargs: fake)
    model = CodexAppServerChatModel(model_name="gpt-test", reasoning_effort="low")

    result = await model.ainvoke(
        [SystemMessage(content="Be exact."), HumanMessage(content="Return ready.")]
    )

    assert result.content == "ready"
    assert result.response_metadata["provider"] == "codex"
    assert result.usage_metadata == {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
    }
    call = fake.calls[0]
    assert call["base_instructions"] == "Be exact."
    assert call["service_name"] == "codex-client-provider"
    assert call["inputs"][0]["type"] == "text"
    assert "[USER]" in call["inputs"][0]["text"]
    assert call["output_schema"]["required"] == ["content"]


@pytest.mark.asyncio
async def test_bound_tool_is_returned_as_langchain_tool_call(monkeypatch):
    fake = FakeClient(
        json.dumps(
            {
                "kind": "tool_call",
                "content": "",
                "tool_name": "add_numbers",
                "tool_arguments_json": json.dumps({"a": 19, "b": 23}),
            }
        )
    )
    monkeypatch.setattr(codex_module, "_client_for_running_loop", lambda **_kwargs: fake)

    @tool
    def add_numbers(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    model = CodexAppServerChatModel(model_name="gpt-test").bind_tools(
        [add_numbers], tool_choice="required"
    )
    result = await model.ainvoke("Add 19 and 23.")

    assert result.tool_calls[0]["name"] == "add_numbers"
    assert result.tool_calls[0]["args"] == {"a": 19, "b": 23}
    call = fake.calls[0]
    assert call["output_schema"]["properties"]["kind"]["enum"] == ["tool_call"]
    assert "add_numbers" in call["developer_instructions"]


@pytest.mark.asyncio
async def test_image_content_is_forwarded_to_app_server(monkeypatch):
    fake = FakeClient(json.dumps({"content": "seen"}))
    monkeypatch.setattr(codex_module, "_client_for_running_loop", lambda **_kwargs: fake)
    model = CodexAppServerChatModel(model_name="gpt-test")

    await model.ainvoke(
        [
            HumanMessage(
                content=[
                    {"type": "text", "text": "Inspect this image."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                ]
            )
        ]
    )

    image_input = fake.calls[0]["inputs"][1]
    assert image_input["type"] == "localImage"
    assert image_input["path"].endswith(".png")
    assert fake.local_image_bytes == [b"\x00"]
    assert not Path(image_input["path"]).exists()


@pytest.mark.asyncio
async def test_large_image_is_downscaled_and_compressed_for_app_server(monkeypatch):
    source = Image.effect_noise((1080, 2400), 80).convert("RGB")
    source_buffer = BytesIO()
    source.save(source_buffer, format="PNG")
    source_bytes = source_buffer.getvalue()
    assert len(source_bytes) > codex_module.CODEX_IMAGE_MAX_BYTES

    fake = FakeClient(json.dumps({"content": "seen"}))
    monkeypatch.setattr(codex_module, "_client_for_running_loop", lambda **_kwargs: fake)
    model = CodexAppServerChatModel(model_name="gpt-test")
    data_url = "data:image/png;base64," + base64.b64encode(source_bytes).decode()

    await model.ainvoke(
        [HumanMessage(content=[{"type": "image_url", "image_url": {"url": data_url}}])]
    )

    image_input = fake.calls[0]["inputs"][1]
    compressed = fake.local_image_bytes[0]
    with Image.open(BytesIO(compressed)) as prepared:
        assert prepared.format == "JPEG"
        assert max(prepared.size) <= codex_module.CODEX_IMAGE_MAX_EDGE
    assert image_input["path"].endswith(".jpg")
    assert len(compressed) <= codex_module.CODEX_IMAGE_MAX_BYTES
    assert len(compressed) < len(source_bytes)
    assert not Path(image_input["path"]).exists()


@pytest.mark.asyncio
async def test_small_image_passes_through_without_reencoding(monkeypatch):
    source = Image.new("RGB", (320, 240), "white")
    source_buffer = BytesIO()
    source.save(source_buffer, format="PNG")
    source_bytes = source_buffer.getvalue()

    fake = FakeClient(json.dumps({"content": "seen"}))
    monkeypatch.setattr(codex_module, "_client_for_running_loop", lambda **_kwargs: fake)
    model = CodexAppServerChatModel(model_name="gpt-test")
    data_url = "data:image/png;base64," + base64.b64encode(source_bytes).decode()

    await model.ainvoke(
        [HumanMessage(content=[{"type": "image_url", "image_url": {"url": data_url}}])]
    )

    image_input = fake.calls[0]["inputs"][1]
    assert image_input["path"].endswith(".png")
    assert fake.local_image_bytes == [source_bytes]
    assert not Path(image_input["path"]).exists()


@pytest.mark.asyncio
async def test_with_structured_output_uses_tool_call_parser(monkeypatch):
    class Verdict(BaseModel):
        ok: bool
        label: str

    fake = FakeClient(
        json.dumps(
            {
                "kind": "tool_call",
                "content": "",
                "tool_name": "Verdict",
                "tool_arguments_json": json.dumps({"ok": True, "label": "ready"}),
            }
        )
    )
    monkeypatch.setattr(codex_module, "_client_for_running_loop", lambda **_kwargs: fake)
    model = CodexAppServerChatModel(model_name="gpt-test").with_structured_output(Verdict)

    result = await model.ainvoke("Return the verdict.")

    assert result == Verdict(ok=True, label="ready")
