"""Shared authentication and origin security contracts."""

import pytest
from unittest.mock import AsyncMock, Mock
from artemis.llm import codex_auth, codex_binary
from artemis.llm.codex_auth import trusted_login_url


@pytest.mark.parametrize(
    "url",
    [
        "https://auth.openai.com/authorize",
        "https://chatgpt.com/authorize",
        "https://chatgpt.com:443/authorize",
    ],
)
def test_official_origins(url):
    assert trusted_login_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "bad-url",
        "http://chatgpt.com/",
        "https://chatgpt.com.attacker.invalid/",
        "https://sub.chatgpt.com/",
        "https://user@chatgpt.com/",
        "https://chatgpt.com:444/",
        "https://chatgpt.com:bad/",
        "https://chatgpt.com/\n",
        "https://chatgpt.com\\@attacker.invalid/",
    ],
)
def test_invalid_origins(url):
    assert not trusted_login_url(url)


@pytest.fixture
def runtime(monkeypatch):
    codex_binary.codex_client_status.cache_clear()
    monkeypatch.setattr(codex_binary, "find_codex_binary", lambda _: "/opt/codex")
    inspect = AsyncMock(return_value={"state": "authenticated", "auth_mode": "chatgpt"})
    monkeypatch.setattr(codex_auth, "inspect_runtime_account", inspect)
    cli = Mock(side_effect=AssertionError("Old CLI login gate must not run"))
    monkeypatch.setattr(codex_binary.provider, "codex_client_status", cli)
    yield inspect, cli
    codex_binary.codex_client_status.cache_clear()


def test_external_login_is_seen_after_negative_result(runtime):
    inspect, cli = runtime
    inspect.side_effect = [{"state": "not_logged_in"}, {"state": "authenticated"}]
    assert not codex_binary.codex_client_status()[0]
    assert codex_binary.codex_client_status()[0]
    assert inspect.await_count == 2
    cli.assert_not_called()


def test_positive_cache_expires_and_observes_logout(runtime, monkeypatch):
    inspect, _ = runtime
    assert codex_binary.codex_client_status()[0]
    assert codex_binary.codex_client_status()[0]
    assert inspect.await_count == 1
    monkeypatch.setattr(codex_binary, "_AUTH_CACHE_SECONDS", 0)
    inspect.return_value = {"state": "not_logged_in"}
    assert not codex_binary.codex_client_status()[0]
    assert inspect.await_count == 2


def test_environment_identity_cannot_reuse_another_runtime_cache(runtime, monkeypatch):
    inspect, _ = runtime
    assert codex_binary.codex_client_status()[0]
    monkeypatch.setenv("CODEX_HOME", "/tmp/codex-test-alternative")
    inspect.return_value = {"state": "not_logged_in"}
    assert not codex_binary.codex_client_status()[0]
    assert inspect.await_count == 2


@pytest.mark.asyncio
async def test_sync_validation_inside_active_loop_uses_rpc_not_cli(runtime):
    from third_party.mobile_use.config.llm import LLM

    inspect, cli = runtime
    inspect.return_value = {"state": "not_required", "auth_mode": None}
    LLM(provider="codex", model="test").validate_provider("Test")
    assert codex_binary.codex_client_status()[0]
    cli.assert_not_called()


def test_auth_errors_never_become_a_success_or_expose_error_text(runtime):
    inspect, _ = runtime
    inspect.side_effect = RuntimeError("PRIVATE-TOKEN")
    ok, detail = codex_binary.codex_client_status()
    assert not ok and "PRIVATE" not in detail
    inspect.side_effect = None
    inspect.return_value = {"state": "authenticated"}
    assert codex_binary.codex_client_status()[0]


@pytest.mark.asyncio
async def test_account_reader_sanitizes_and_honors_auth_not_required():
    client = Mock()
    client.request = AsyncMock(
        return_value={"account": {"type": "chatgpt", "email": "PRIVATE", "accessToken": "SECRET"}}
    )
    report = await codex_auth.read_account(client)
    assert report == {"state": "authenticated", "auth_mode": "chatgpt"}
    assert "PRIVATE" not in str(report) and "SECRET" not in str(report)
    client.request.return_value = {"account": None, "requiresOpenaiAuth": False}
    assert (await codex_auth.read_account(client))["state"] == "not_required"
    client.request.return_value = {"data": []}
    with pytest.raises(ValueError):
        await codex_auth.read_account(client)
