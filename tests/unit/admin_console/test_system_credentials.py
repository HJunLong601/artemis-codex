"""Console credentials follow primary routes and never expose login output."""

from unittest.mock import Mock

import pytest
from httpx import ASGITransport, AsyncClient

from apps.admin_console.server import app
from artemis.config import settings
from artemis.config.llm import LLMConfig, _expand_default_into_nodes


@pytest.fixture
def credentials(monkeypatch):
    config = LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {
                    "provider": "google",
                    "model": "test",
                    "fallback": {"provider": "codex", "model": "test"},
                }
            }
        )
    )
    parse = Mock(return_value=config)
    login = Mock(return_value=(True, "PRIVATE-LOGIN-DETAIL"))
    monkeypatch.setattr(type(settings), "get_api_key", lambda self, provider: None)
    monkeypatch.setattr("artemis.config.llm.parse_llm_config", parse)
    monkeypatch.setattr("artemis.llm.codex_app_server.codex_client_status", login)
    return config, parse, login


async def credential_status():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.get("/api/system/credentials")
    assert response.status_code == 200
    assert "PRIVATE-LOGIN-DETAIL" not in response.text
    return {entry["name"]: entry["configured"] for entry in response.json()["providers"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("node", ["planner", "operator", "checker", "utils.outputter"])
async def test_codex_login_on_any_primary_node_is_refreshed(credentials, node):
    config, _, login = credentials
    model = dict(config.iter_primary_models())[node]
    model.provider = "codex"
    login.side_effect = [(False, "PRIVATE-LOGIN-DETAIL"), (True, "PRIVATE-LOGIN-DETAIL")]

    assert (await credential_status())["codex"] is False
    assert (await credential_status())["codex"] is True
    assert login.cache_clear.call_count == 2
    assert login.call_count == 2


@pytest.mark.asyncio
async def test_fallback_only_codex_does_not_probe_login(credentials):
    _, _, login = credentials
    assert (await credential_status())["codex"] is False
    login.assert_not_called()
    login.cache_clear.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_routing_does_not_claim_codex_is_ready(credentials):
    _, parse, login = credentials
    parse.side_effect = ValueError("bad configuration")
    assert (await credential_status())["codex"] is False
    login.assert_not_called()


@pytest.mark.asyncio
async def test_xai_presence_is_reported_without_key_material(credentials, monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(
        type(settings),
        "get_api_key",
        lambda self, provider: SecretStr("PRIVATE-LOGIN-DETAIL") if provider == "xai" else None,
    )
    status = await credential_status()
    assert status["xai"] is True
    assert status["google"] is False
