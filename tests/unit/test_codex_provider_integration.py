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

"""Coverage for the thin Artemis integration around the reusable provider."""

import json

from langchain_core.messages import HumanMessage
import pytest

import codex_client_provider.langchain as provider_module
from codex_client_provider import CodexAppServerChatModel as GenericCodexChatModel

from artemis.llm.codex_app_server import CodexAppServerChatModel
from artemis.llm.router import ModelEndpoint, ModelFactory, ModelProvider


def test_telemetry_kill_switch(monkeypatch):
    monkeypatch.setenv("ARTEMIS_CODEX_TELEMETRY", "0")
    assert not CodexAppServerChatModel(model_name="test").telemetry_enabled


@pytest.mark.asyncio
async def test_missing_extension_hook_uses_original_provider(monkeypatch):
    from unittest.mock import AsyncMock

    original = AsyncMock(return_value="original-result")
    monkeypatch.setattr(provider_module, "_parse_response", None)
    monkeypatch.setattr(GenericCodexChatModel, "_agenerate", original)
    model = CodexAppServerChatModel(model_name="test")
    assert await model._agenerate([]) == "original-result"
    original.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_usage_is_not_shared_between_requests(monkeypatch):
    import asyncio

    class Client:
        async def run_completion(self, **kw):
            await asyncio.sleep(0)
            cached = 60 if kw["model"] == "first" else 10
            return {
                "text": '{"content":"ok"}',
                "model": kw["model"],
                "usage": {"last": {"inputTokens": 100, "cachedInputTokens": cached}},
            }

    monkeypatch.setattr(provider_module, "_client_for_running_loop", lambda **_: Client())
    first, second = await asyncio.gather(
        *[
            CodexAppServerChatModel(model_name=name).ainvoke([HumanMessage(content="test")])
            for name in ("first", "second")
        ]
    )
    assert first.usage_metadata["input_token_details"]["cache_read"] == 60
    assert second.usage_metadata["input_token_details"]["cache_read"] == 10


@pytest.mark.parametrize(
    "cached,expected", [(0, 0), (60, 60), (-1, None), (101, None), ("bad", None), (None, None)]
)
@pytest.mark.asyncio
async def test_cache_metadata_never_breaks_output(monkeypatch, cached, expected):
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.run_completion.return_value = {
        "text": '{"content":"ready"}',
        "model": "test",
        "usage": {
            "last": {
                "inputTokens": 100,
                "outputTokens": 4,
                "totalTokens": 104,
                "cachedInputTokens": cached,
            }
        },
    }
    monkeypatch.setattr(provider_module, "_client_for_running_loop", lambda **_: client)
    result = await CodexAppServerChatModel(model_name="test").ainvoke(
        [HumanMessage(content="test")]
    )
    assert result.content == "ready"
    assert result.usage_metadata.get("input_token_details", {}).get("cache_read") == expected
    assert result.response_metadata["cache_usage_available"] is (expected is not None)
    assert result.response_metadata["inference_seconds"] >= 0
    client.run_completion.assert_awaited_once()


@pytest.mark.asyncio
async def test_telemetry_preserves_tool_contract_and_has_explicit_legacy_switch(monkeypatch):
    from unittest.mock import AsyncMock
    from langchain_core.tools import tool

    @tool
    def locate(text: str) -> str:
        """Find the visible text."""
        return text

    client = AsyncMock()
    client.run_completion.return_value = {
        "text": json.dumps(
            {"kind": "tool_call", "tool_name": "locate", "tool_arguments_json": '{"text":"显示"}'}
        ),
        "model": "test",
        "usage": None,
    }
    monkeypatch.setattr(provider_module, "_client_for_running_loop", lambda **_: client)
    messages = [HumanMessage(content="Find display")]
    new = (
        await CodexAppServerChatModel(model_name="test")
        .bind_tools([locate], tool_choice="required")
        .ainvoke(messages)
    )
    new_args = client.run_completion.call_args.kwargs
    old = (
        await CodexAppServerChatModel(model_name="test", telemetry_enabled=False)
        .bind_tools([locate], tool_choice="required")
        .ainvoke(messages)
    )
    assert client.run_completion.call_args.kwargs == new_args
    assert [(c["name"], c["args"]) for c in new.tool_calls] == [
        (c["name"], c["args"]) for c in old.tool_calls
    ]
    assert "inference_seconds" not in old.response_metadata


@pytest.mark.asyncio
async def test_provider_error_is_not_replayed_for_telemetry(monkeypatch):
    from unittest.mock import AsyncMock

    client = AsyncMock()
    client.run_completion.side_effect = RuntimeError("provider rejected request")
    monkeypatch.setattr(provider_module, "_client_for_running_loop", lambda **_: client)
    with pytest.raises(RuntimeError, match="rejected"):
        await CodexAppServerChatModel(model_name="test").ainvoke([HumanMessage(content="test")])
    client.run_completion.assert_awaited_once()


def test_router_builds_artemis_codex_model_without_api_key():
    endpoint = ModelEndpoint(
        provider=ModelProvider.CODEX,
        model_name="gpt-test",
        reasoning_effort="medium",
    )

    model = ModelFactory.create_model(endpoint)

    assert isinstance(model, CodexAppServerChatModel)
    assert isinstance(model, GenericCodexChatModel)
    assert model.model_name == "gpt-test"
    assert model.client_name == "artemis"
    assert model.service_name == "artemis"


@pytest.mark.asyncio
async def test_artemis_identity_is_forwarded_to_the_generic_provider(monkeypatch):
    calls = []

    class FakeClient:
        async def run_completion(self, **kwargs):
            calls.append(kwargs)
            return {
                "text": json.dumps({"content": "ready"}),
                "thread_id": "thread-test",
                "model": "gpt-test",
                "usage": None,
            }

    def fake_client(**kwargs):
        assert kwargs["client_name"] == "artemis"
        assert kwargs["client_title"] == "Artemis"
        assert kwargs["client_version"] == "1.0"
        return FakeClient()

    monkeypatch.setattr(provider_module, "_client_for_running_loop", fake_client)
    model = CodexAppServerChatModel(model_name="gpt-test")

    result = await model.ainvoke([HumanMessage(content="Return ready.")])

    assert result.content == "ready"
    assert calls[0]["service_name"] == "artemis"
