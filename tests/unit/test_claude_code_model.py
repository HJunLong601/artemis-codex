"""Provider boundary tests: real messages and tools, no model/account required."""

import base64
from io import BytesIO
from unittest.mock import AsyncMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from PIL import Image
import pytest

from artemis.llm.claude_code import ClaudeCodeChatModel
from artemis.llm.claude_code_client import ClaudeCodeError


@pytest.fixture
def completion(monkeypatch):
    call = AsyncMock(
        return_value={
            "structured_output": {"content": "READY", "tool_calls": []},
            "usage": {
                "input_tokens": 5,
                "cache_read_input_tokens": 7,
                "cache_creation_input_tokens": 3,
                "output_tokens": 2,
            },
            "modelUsage": {"claude-sonnet-test": {}},
            "session_id": "fixture",
        }
    )
    monkeypatch.setattr("artemis.llm.claude_code.run_claude_completion", call)
    return call


@pytest.mark.asyncio
async def test_text_system_history_and_cache_telemetry(completion):
    model = ClaudeCodeChatModel(model_name="sonnet")
    result = await model.ainvoke(
        [
            SystemMessage("SYSTEM"),
            HumanMessage("first"),
            AIMessage(content="", tool_calls=[{"name": "tap", "args": {"x": 2}, "id": "a"}]),
            ToolMessage(content="tapped", tool_call_id="a"),
            HumanMessage("next"),
        ]
    )
    request = completion.call_args.kwargs
    assert "SYSTEM" in request["system_prompt"]
    transcript = str(request["content"])
    assert all(value in transcript for value in ("first", "tap", "tapped", "next", "a"))
    assert result.content == "READY"
    assert result.usage_metadata["input_tokens"] == 15
    assert result.usage_metadata["total_tokens"] == 17
    assert result.usage_metadata["input_token_details"]["cache_read"] == 7
    assert result.response_metadata["provider"] == "claude_code"
    assert result.response_metadata["model_name"] == "claude-sonnet-test"


@pytest.mark.asyncio
async def test_image_is_native_base64_and_original_history_is_not_mutated(completion):
    buffer = BytesIO()
    Image.new("RGB", (100, 200), "blue").save(buffer, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    message = HumanMessage(
        content=[
            {"type": "text", "text": "screen"},
            {"type": "image_url", "image_url": {"url": url}},
        ]
    )
    await ClaudeCodeChatModel().ainvoke([message])
    blocks = completion.call_args.kwargs["content"]
    image = next(block for block in blocks if block["type"] == "image")
    assert image["source"]["type"] == "base64"
    assert image["source"]["media_type"] == "image/webp"
    assert message.content[1]["image_url"]["url"] == url


TOOL = {
    "name": "tap",
    "description": "Tap a point",
    "parameters": {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    },
}


@pytest.mark.asyncio
async def test_host_tools_are_structured_results_not_cli_executable_tools(completion):
    completion.return_value["structured_output"] = {
        "content": "",
        "tool_calls": [{"name": "tap", "args": {"x": 42}}],
    }
    result = await ClaudeCodeChatModel().bind_tools([TOOL], tool_choice="required").ainvoke("tap")
    assert result.tool_calls[0]["name"] == "tap"
    assert result.tool_calls[0]["args"] == {"x": 42}
    assert result.tool_calls[0]["id"]
    assert completion.call_args.kwargs["output_schema"]["properties"]["tool_calls"]["minItems"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "calls", [[], [{"name": "delete", "args": {}}], [{"name": "tap", "args": {"x": "bad"}}]]
)
async def test_invalid_or_missing_required_tool_never_reaches_host(completion, calls):
    completion.return_value["structured_output"] = {"content": "", "tool_calls": calls}
    with pytest.raises(ClaudeCodeError):
        await ClaudeCodeChatModel().bind_tools([TOOL], tool_choice="required").ainvoke("tap")


@pytest.mark.asyncio
async def test_remote_images_are_rejected_without_fetching(completion):
    with pytest.raises(ClaudeCodeError):
        await ClaudeCodeChatModel().ainvoke(
            [
                HumanMessage(
                    content=[
                        {
                            "type": "image_url",
                            "image_url": "https://example.invalid/private.png",
                        }
                    ]
                )
            ]
        )
    completion.assert_not_called()


@pytest.mark.asyncio
async def test_missing_usage_is_not_fabricated(completion):
    completion.return_value.pop("usage")
    result = await ClaudeCodeChatModel().ainvoke("READY")
    assert result.usage_metadata is None
    assert result.response_metadata["cache_usage_available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [{}, {"input_tokens": True, "output_tokens": 2}, {"input_tokens": -1, "output_tokens": 2}],
)
async def test_incomplete_or_invalid_usage_is_not_fabricated(completion, usage):
    completion.return_value["usage"] = usage
    assert (await ClaudeCodeChatModel().ainvoke("READY")).usage_metadata is None


def test_factory_keeps_anthropic_separate(monkeypatch):
    from artemis.llm.router import ModelEndpoint, ModelFactory, ModelProvider

    monkeypatch.delenv("ARTEMIS_FAKE_LLM", raising=False)
    assert ModelProvider.from_string("claude_code").value == "claude_code"
    assert ModelProvider.from_string("claude").value == "anthropic"
    model = ModelFactory.create_model(ModelEndpoint(provider="claude_code", model_name="sonnet"))
    assert isinstance(model, ClaudeCodeChatModel)
