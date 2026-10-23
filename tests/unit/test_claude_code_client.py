"""Exercise the public CLI contract through real, controlled subprocesses."""

import asyncio
import json
from pathlib import Path
import sys

import pytest

from artemis.llm.claude_code_client import (
    ClaudeCodeError,
    find_claude_code_binary,
    probe_claude_code,
    run_claude_completion,
)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    binary = tmp_path / "claude-fixture"
    binary.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys, time
args = sys.argv[1:]
mode = os.environ.get("CLAUDE_FIXTURE_MODE", "ok")
if "--version" in args:
    print("2.1.309 (Claude Code)"); sys.exit(0)
if "--help" in args:
    print("--safe-mode --tools --strict-mcp-config --input-format --output-format --json-schema --no-session-persistence --system-prompt-file --effort"); sys.exit(0)
if args == ["auth", "status"]:
    print(json.dumps({"loggedIn": mode != "unauth", "authMethod": "claude.ai", "email": "PRIVATE", "accessToken": "SECRET"})); sys.exit(1 if mode == "unauth" else 0)
data = sys.stdin.read()
if os.environ.get("CLAUDE_FIXTURE_CAPTURE"):
    with open(os.environ["CLAUDE_FIXTURE_CAPTURE"], "w") as f:
        json.dump({"args": args, "input": json.loads(data), "pid": os.getpid()}, f)
if os.environ.get("CLAUDE_FIXTURE_CAPTURE_DIR"):
    with open(os.path.join(os.environ["CLAUDE_FIXTURE_CAPTURE_DIR"], str(os.getpid()) + ".json"), "w") as f:
        json.dump({"args": args, "input": json.loads(data), "pid": os.getpid()}, f)
if mode == "hang": time.sleep(100)
if mode == "overflow": print("x" * (17 * 1024 * 1024)); sys.exit(0)
if mode == "auth_error":
    print(json.dumps({"type":"result", "is_error":True, "result":"Not logged in PRIVATE SECRET"})); sys.exit(1)
if mode == "quota":
    print(json.dumps({"type":"result", "is_error":True, "result":"Rate limit reached PRIVATE SECRET"})); sys.exit(1)
if mode == "malformed": print("PRIVATE SECRET not-json"); sys.exit(0)
if mode == "missing_result": print(json.dumps({"type":"system", "subtype":"init"})); sys.exit(0)
schema = json.loads(args[args.index("--json-schema")+1])
print(json.dumps({"type":"system", "subtype":"init", "session_id":"fixture"}))
print(json.dumps({"type":"result", "is_error":False, "structured_output":json.loads(os.environ.get("CLAUDE_FIXTURE_RESULT", '{"content":"READY"}')), "usage":{"input_tokens":5,"output_tokens":2,"cache_read_input_tokens":7,"cache_creation_input_tokens":3}, "modelUsage":{"claude-sonnet-test":{}}, "session_id":"fixture"}))
"""
    )
    binary.chmod(0o700)
    monkeypatch.setenv("ARTEMIS_CLAUDE_CODE_BIN", str(binary))
    return binary


SCHEMA = {
    "type": "object",
    "properties": {"content": {"type": "string"}},
    "required": ["content"],
    "additionalProperties": False,
}


async def complete(binary, timeout=5):
    return await run_claude_completion(
        binary=str(binary),
        model="sonnet",
        effort="low",
        system_prompt="PRIVATE SYSTEM PROMPT",
        content=[{"type": "text", "text": "READY"}],
        output_schema=SCHEMA,
        timeout=timeout,
    )


def test_explicit_binary_override_is_not_silently_replaced(cli):
    assert find_claude_code_binary() == str(cli)
    assert find_claude_code_binary("/missing/claude") is None


def test_invalid_explicit_status_path_cannot_use_a_different_authenticated_client(cli):
    from artemis.llm.claude_code_client import claude_code_status

    claude_code_status.cache_clear()
    assert claude_code_status(str(cli))[0]
    assert not claude_code_status("/missing/claude")[0]


@pytest.mark.asyncio
async def test_probe_uses_official_status_and_does_not_expose_identity(cli, monkeypatch):
    report = await probe_claude_code()
    assert report["state"] == "authenticated"
    assert report["version"] == "2.1.309"
    assert "PRIVATE" not in str(report) and "SECRET" not in str(report)
    monkeypatch.setenv("CLAUDE_FIXTURE_MODE", "unauth")
    assert (await probe_claude_code())["state"] == "not_logged_in"


@pytest.mark.asyncio
async def test_protocol_is_isolated_and_system_prompt_is_not_in_argv(cli, tmp_path, monkeypatch):
    capture = tmp_path / "captured.json"
    monkeypatch.setenv("CLAUDE_FIXTURE_CAPTURE", str(capture))
    result = await complete(cli)
    assert result["structured_output"] == {"content": "READY"}
    assert result["usage"]["cache_read_input_tokens"] == 7
    captured = json.loads(capture.read_text())
    args = captured["args"]
    assert "--safe-mode" in args and "--strict-mcp-config" in args
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--disallowedTools") + 1] == "mcp__*"
    assert "--no-session-persistence" in args and "--bare" not in args
    assert "--dangerously-skip-permissions" not in args
    assert "PRIVATE SYSTEM PROMPT" not in str(args)
    assert not Path(args[args.index("--system-prompt-file") + 1]).exists()
    assert captured["input"]["type"] == "user"
    assert captured["input"]["message"]["content"] == [{"type": "text", "text": "READY"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,code",
    [
        ("auth_error", 401),
        ("quota", 429),
        ("malformed", 502),
        ("missing_result", 502),
        ("overflow", 502),
    ],
)
async def test_failures_are_classified_and_never_expose_raw_output(cli, monkeypatch, mode, code):
    monkeypatch.setenv("CLAUDE_FIXTURE_MODE", mode)
    with pytest.raises(ClaudeCodeError) as error:
        await complete(cli)
    assert error.value.status_code == code
    assert "PRIVATE" not in str(error.value) and "SECRET" not in str(error.value)


@pytest.mark.asyncio
async def test_timeout_and_cancellation_remove_private_workspaces(cli, monkeypatch, tmp_path):
    import psutil

    capture = tmp_path / "cancelled.json"
    monkeypatch.setenv("CLAUDE_FIXTURE_CAPTURE", str(capture))
    monkeypatch.setenv("CLAUDE_FIXTURE_MODE", "hang")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    with pytest.raises(TimeoutError):
        await complete(cli, timeout=0.2)
    request = json.loads(capture.read_text())
    assert not psutil.pid_exists(request["pid"])
    assert not Path(request["args"][request["args"].index("--system-prompt-file") + 1]).exists()
    task = asyncio.create_task(complete(cli))
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    request = json.loads(capture.read_text())
    assert not psutil.pid_exists(request["pid"])
    assert not Path(request["args"][request["args"].index("--system-prompt-file") + 1]).exists()
    assert not list(tmp_path.glob("artemis-claude-*"))


@pytest.mark.asyncio
async def test_concurrent_calls_keep_private_workspaces_and_histories_separate(
    cli, monkeypatch, tmp_path
):
    monkeypatch.setenv("CLAUDE_FIXTURE_CAPTURE_DIR", str(tmp_path))
    await asyncio.gather(
        *[
            run_claude_completion(
                binary=str(cli),
                model="sonnet",
                system_prompt=f"system-{index}",
                content=[{"type": "text", "text": f"task-{index}"}],
                output_schema=SCHEMA,
            )
            for index in range(3)
        ]
    )
    requests = [json.loads(path.read_text()) for path in tmp_path.glob("*.json")]
    assert len(requests) == 3
    assert {item["input"]["message"]["content"][0]["text"] for item in requests} == {
        "task-0",
        "task-1",
        "task-2",
    }
    prompts = [
        Path(item["args"][item["args"].index("--system-prompt-file") + 1]) for item in requests
    ]
    assert len(set(prompts)) == 3
    assert all(not path.exists() for path in prompts)


@pytest.mark.asyncio
async def test_official_protocol_through_real_factory_gateway_and_flash_binding(cli, monkeypatch):
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage, ToolMessage
    from artemis.agents.flash.runner import FlashRunner
    from artemis.config.llm import LLMConfig, _expand_default_into_nodes
    from artemis.services.llm import get_llm
    from artemis.llm.model_preflight import require_available_models

    monkeypatch.delenv("ARTEMIS_FAKE_LLM", raising=False)
    config = LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {
                    "provider": "claude_code",
                    "model": "sonnet",
                    "fallback": {"provider": "claude_code", "model": "haiku"},
                }
            }
        )
    )
    assert (await require_available_models(config))["ok"]
    context = SimpleNamespace(llm_config=config, data_engine=None)
    model = get_llm(context, "operator")
    runner = object.__new__(FlashRunner)
    tool = {
        "name": "report_task_status",
        "description": "Report task completion",
        "parameters": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
        },
    }
    monkeypatch.setenv(
        "CLAUDE_FIXTURE_RESULT",
        json.dumps(
            {
                "content": "",
                "tool_calls": [{"name": "report_task_status", "args": {"status": "completed"}}],
            }
        ),
    )
    result = await runner._invoke_model(
        model, [tool], [HumanMessage("Synthetic protocol check; no device actions.")]
    )
    assert result.tool_calls[0]["args"] == {"status": "completed"}
    assert result.usage_metadata["input_token_details"]["cache_read"] == 7
    monkeypatch.setenv("CLAUDE_FIXTURE_RESULT", json.dumps({"content": "done", "tool_calls": []}))
    final = await model.ainvoke(
        [
            HumanMessage("Finish"),
            result,
            ToolMessage(content="completed", tool_call_id=result.tool_calls[0]["id"]),
        ]
    )
    assert final.content == "done"


@pytest.mark.asyncio
async def test_structured_output_uses_the_same_controlled_cli(cli, monkeypatch):
    from pydantic import BaseModel
    from artemis.llm.claude_code import ClaudeCodeChatModel

    class Answer(BaseModel):
        status: str

    monkeypatch.setenv(
        "CLAUDE_FIXTURE_RESULT",
        json.dumps(
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "Answer",
                        "args": {"status": "READY"},
                    }
                ],
            }
        ),
    )
    result = await ClaudeCodeChatModel().with_structured_output(Answer).ainvoke("READY")
    assert isinstance(result, Answer) and result.status == "READY"


@pytest.mark.asyncio
async def test_shared_sdk_worker_gate_rejects_signed_out_cli_before_device_mutation(
    cli, monkeypatch
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    from artemis.config.llm import LLMConfig, _expand_default_into_nodes
    from artemis.graph.state import State
    from artemis.llm.model_preflight import ModelPreflightError
    from third_party.mobile_use.context import DeviceContext
    from third_party.mobile_use.sdk import agent as worker
    from third_party.mobile_use.sdk.types.task import TaskRequest

    monkeypatch.delenv("ARTEMIS_FAKE_LLM", raising=False)
    monkeypatch.delenv("ARTEMIS_CLOUD_MODE", raising=False)
    monkeypatch.setenv("CLAUDE_FIXTURE_MODE", "unauth")
    cfg = LLMConfig.model_validate(
        _expand_default_into_nodes(
            {
                "default": {
                    "provider": "claude_code",
                    "model": "sonnet",
                    "fallback": {"provider": "claude_code", "model": "haiku"},
                }
            }
        )
    )
    lock = MagicMock()
    lock.get_active_owner.return_value = None
    monkeypatch.setattr(worker, "DeviceExecutionLock", lock)
    mutate = AsyncMock(side_effect=AssertionError("Device touched before authentication"))
    agent = SimpleNamespace(
        _initialized=True,
        _session_id="unit-session",
        _tasks=[],
        _adb_client=None,
        _ui_adb_client=None,
        _device_context=DeviceContext(device_id="unit-device"),
        _config=SimpleNamespace(
            default_profile=SimpleNamespace(llm_config=cfg),
            agent_profiles={},
            concurrency_mode="per_device",
            max_concurrency=None,
        ),
        _get_graph_state=lambda **_: State.initial("Synthetic startup check"),
        _connect_device_driver=mutate,
        _finalize_tracing_safely=AsyncMock(),
        _task_lock=asyncio.Lock(),
        _current_task=None,
        _watch_external_cancel=AsyncMock(),
    )
    with pytest.raises(ModelPreflightError, match="Claude Code"):
        await worker.AgentBase._run_task(
            agent, TaskRequest(goal="Synthetic startup check", profile="flash")
        )
    mutate.assert_not_called()
    lock.return_value.acquire.assert_not_called()
    assert agent._tasks[0].status == "failed"
