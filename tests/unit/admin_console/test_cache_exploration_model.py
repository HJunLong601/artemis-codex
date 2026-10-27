from types import SimpleNamespace

import pytest

from artemis.runtime.cache_exploration.model import strict_usage


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": 20},
        {"input_tokens": True, "output_tokens": 3, "total_tokens": 4},
        {"input_tokens": 20, "output_tokens": 3, "total_tokens": 2},
    ],
)
def test_incomplete_usage_is_not_zero(usage):
    assert strict_usage(usage) is None


def test_cached_tokens_not_added_twice():
    assert (
        strict_usage(
            {
                "input_tokens": 20,
                "output_tokens": 3,
                "total_tokens": 23,
                "input_token_details": {"cache_read": 10},
            }
        )
        == 23
    )


def test_reasoning_in_total_not_counted_twice():
    assert (
        strict_usage(
            {
                "input_tokens": 20,
                "output_tokens": 3,
                "total_tokens": 30,
                "output_token_details": {"reasoning": 7},
            }
        )
        == 30
    )


@pytest.mark.asyncio
async def test_task_owned_client_is_reused_without_hidden_retries_and_closed(monkeypatch):
    from unittest.mock import AsyncMock, Mock
    from artemis.runtime.cache_exploration import model

    fake = SimpleNamespace(
        max_retries=0,
        async_client=SimpleNamespace(aclose=AsyncMock()),
        ainvoke=AsyncMock(
            return_value=SimpleNamespace(
                content="{}",
                usage_metadata={"input_tokens": 20, "output_tokens": 3, "total_tokens": 23},
            )
        ),
    )
    create = Mock(return_value=fake)
    monkeypatch.setattr(model.ModelFactory, "create_model", create)
    caller = model.Caller({"operator": {"provider": "openai", "model": "test-model"}})
    assert (await caller.call("operator", "test", b"fake")).tokens == 23
    assert (await caller.call("operator", "test", b"fake")).tokens == 23
    assert create.call_count == 1
    assert create.call_args.args[0].max_retries == 0
    await caller.close()
    fake.async_client.aclose.assert_awaited_once()


def test_provider_alias_is_normalized_before_freezing_model_routes(monkeypatch):
    from artemis.runtime.cache_exploration import model

    cfg = SimpleNamespace(
        provider="codex-client",
        model="test-model",
        reasoning_effort=None,
        temperature=0,
        thinking_budget=None,
        thinking_level=None,
    )
    monkeypatch.setattr(
        model, "parse_llm_config", lambda: SimpleNamespace(operator=cfg, get_utils=lambda role: cfg)
    )
    assert model.routes()["operator"]["provider"] == "codex"


@pytest.mark.asyncio
async def test_broken_client_close_still_terminates_its_owned_process():
    from unittest.mock import AsyncMock, Mock
    from artemis.runtime.cache_exploration.model import Caller

    process = SimpleNamespace(returncode=None, terminate=Mock(), kill=Mock())

    async def wait():
        process.returncode = 0

    process.wait = wait
    caller = Caller({})
    caller.client = SimpleNamespace(
        process=process, close=AsyncMock(side_effect=RuntimeError("close failed"))
    )
    await caller.close()
    process.terminate.assert_called_once()
    assert caller.client is None
