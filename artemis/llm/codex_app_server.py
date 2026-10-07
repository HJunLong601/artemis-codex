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

"""Artemis integration for the reusable Codex client provider.

Protocol, process pooling and multimodal conversion live in the independently
published ``codex_client_provider`` package. This module supplies Artemis identity
and a pinned-0.1.0 response bridge that preserves cache counters until the public
provider exposes them. The original provider remains available as a kill switch.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

import codex_client_provider.langchain as provider
from codex_client_provider.langchain import (
    CODEX_IMAGE_JPEG_QUALITY,
    CODEX_IMAGE_MAX_BYTES,
    CODEX_IMAGE_MAX_EDGE,
    CODEX_IMAGE_MIN_JPEG_QUALITY,
    CodexAppServerChatModel as _CodexAppServerChatModel,
    CodexAppServerClient,
    CodexAppServerError,
    codex_client_status,
    find_codex_binary,
)


class CodexAppServerChatModel(_CodexAppServerChatModel):
    """Codex App Server model carrying Artemis identity in protocol metadata."""

    client_name: str = Field(default="artemis")
    client_title: str = Field(default="Artemis")
    client_version: str = Field(default="1.0")
    service_name: str = Field(default="artemis")
    telemetry_enabled: bool = Field(
        default_factory=lambda: os.getenv("ARTEMIS_CODEX_TELEMETRY", "1") != "0"
    )

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        # Compatibility bridge for pinned provider 0.1.0, which discards raw
        # cache usage. Reuse its transport, pool, prompts, images and contract.
        # No global monkeypatch and no second inference on telemetry failure.
        helpers = (
            "_response_contract",
            "_message_transcript",
            "_system_instructions",
            "_client_for_running_loop",
            "_parse_response",
        )
        if not self.telemetry_enabled or not all(
            callable(getattr(provider, n, None)) for n in helpers
        ):
            return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        started = time.monotonic()
        tools = list(kwargs.get("tools") or [])
        schema, contract = provider._response_contract(tools, kwargs.get("tool_choice"))
        transcript, images, temp_paths = provider._message_transcript(messages)
        developer_parts = [contract]
        if tools:
            developer_parts.append(
                "Available host tools:\n" + json.dumps(tools, ensure_ascii=False, default=str)
            )
        try:
            result = await provider._client_for_running_loop(
                binary=self.codex_binary,
                client_name=self.client_name,
                client_title=self.client_title,
                client_version=self.client_version,
            ).run_completion(
                model=self.model_name,
                effort=self.reasoning_effort,
                cwd=str(Path(self.cwd).resolve()),
                base_instructions=provider._system_instructions(messages),
                developer_instructions="\n\n".join(developer_parts),
                inputs=[
                    {
                        "type": "text",
                        "text": transcript or "Continue from the supplied instructions.",
                    },
                    *images,
                ],
                output_schema=schema,
                timeout_seconds=self.timeout_seconds,
                service_name=self.service_name,
            )
        finally:
            for path in temp_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).debug("Could not remove temporary Codex image")

        parsed = provider._parse_response(result["text"])
        metadata = {
            "provider": "codex",
            "model": result.get("model") or self.model_name,
            "thread_id": result.get("thread_id"),
            "inference_seconds": round(time.monotonic() - started, 6),
        }
        usage, cache_available = _usage_metadata(result.get("usage"))
        metadata["cache_usage_available"] = cache_available
        tool_calls = []
        if tools and parsed.get("kind") == "tool_call":
            raw_args = parsed.get("tool_arguments_json") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError as exc:
                    raise CodexAppServerError(
                        f"Codex returned invalid JSON arguments for tool {parsed.get('tool_name')!r}: {raw_args}"
                    ) from exc
            else:
                args = raw_args
            tool_calls = [
                {
                    "name": str(parsed.get("tool_name")),
                    "args": args,
                    "id": f"call_{uuid4().hex}",
                    "type": "tool_call",
                }
            ]
            content = parsed.get("content") or ""
        else:
            content = str(parsed.get("content") or result["text"])
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=content,
                        tool_calls=tool_calls,
                        response_metadata=metadata,
                        usage_metadata=usage,
                    )
                )
            ]
        )


def _usage_metadata(raw):
    """Malformed/absent optional counters must never invalidate model output."""
    last = raw.get("last") if isinstance(raw, dict) else None
    if not isinstance(last, dict) or not last:
        return None, False

    def counter(name):
        value = last.get(name)
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )

    usage = {
        "input_tokens": counter("inputTokens") or 0,
        "output_tokens": counter("outputTokens") or 0,
        "total_tokens": counter("totalTokens") or 0,
    }
    cached = counter("cachedInputTokens")
    if cached is not None and cached <= usage["input_tokens"]:
        usage["input_token_details"] = {"cache_read": cached}
        return usage, True
    return usage, False


__all__ = [
    "CODEX_IMAGE_JPEG_QUALITY",
    "CODEX_IMAGE_MAX_BYTES",
    "CODEX_IMAGE_MAX_EDGE",
    "CODEX_IMAGE_MIN_JPEG_QUALITY",
    "CodexAppServerChatModel",
    "CodexAppServerClient",
    "CodexAppServerError",
    "codex_client_status",
    "find_codex_binary",
]
