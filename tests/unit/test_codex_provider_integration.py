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
