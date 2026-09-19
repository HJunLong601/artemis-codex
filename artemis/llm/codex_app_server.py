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

"""LangChain adapter for a locally signed-in Codex client.

The adapter talks to ``codex app-server`` over JSONL/stdio. Authentication is
owned by the installed Codex client, so Artemis never reads, copies, or stores
the ChatGPT session token. Artemis tools remain in charge of execution: their
schemas are presented to Codex as a constrained output contract and converted
back into ordinary LangChain tool calls.
"""

from __future__ import annotations

import asyncio
import atexit
import base64
from collections.abc import Callable, Sequence
from functools import lru_cache
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any
from urllib.parse import unquote_to_bytes
from uuid import uuid4
import weakref

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import Field

from artemis.utils.logger import get_logger

logger = get_logger(__name__)


def find_codex_binary() -> str | None:
    """Return the installed Codex executable without inspecting its credentials."""
    configured = os.environ.get("ARTEMIS_CODEX_BIN", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None

    for name in ("codex.exe", "codex"):
        found = shutil.which(name)
        if found:
            return found

    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            desktop_binary = (
                Path(local_app_data) / "Programs" / "OpenAI" / "Codex" / "bin" / "codex.exe"
            )
            if desktop_binary.is_file():
                return str(desktop_binary)
    return None


def _hidden_process_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


@lru_cache(maxsize=1)
def codex_client_status() -> tuple[bool, str]:
    """Check CLI presence and login status without reading any auth files."""
    binary = find_codex_binary()
    if not binary:
        return False, "Codex CLI was not found. Install/open Codex and sign in first."
    try:
        result = subprocess.run(
            [binary, "login", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            check=False,
            creationflags=_hidden_process_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not query Codex login status: {exc}"

    output = " ".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode == 0 and "logged in" in output.lower():
        return True, output or "Logged in"
    return False, output or "Codex is installed but is not signed in."


class CodexAppServerError(RuntimeError):
    """Raised when the local Codex app-server cannot serve a completion."""


class CodexAppServerClient:
    """One persistent JSON-RPC app-server connection for an asyncio loop."""

    def __init__(self, binary: str):
        self.binary = binary
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._thread_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return
        async with self._start_lock:
            if self.process and self.process.returncode is None:
                return
            try:
                self.process = await asyncio.create_subprocess_exec(
                    self.binary,
                    "app-server",
                    "--stdio",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=_hidden_process_flags(),
                )
            except OSError as exc:
                raise CodexAppServerError(f"Could not start Codex app-server: {exc}") from exc

            self._reader_task = asyncio.create_task(self._read_stdout())
            self._stderr_task = asyncio.create_task(self._read_stderr())
            try:
                await self.request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": "artemis",
                            "title": "Artemis",
                            "version": "1.0",
                        },
                        "capabilities": {"experimentalApi": False},
                    },
                    ensure_started=False,
                )
                await self.notify("initialized", {}, ensure_started=False)
            except Exception:
                await self.close()
                raise

    async def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        ensure_started: bool = True,
    ) -> Any:
        if ensure_started:
            await self.start()
        process = self.process
        if not process or process.returncode is not None or process.stdin is None:
            raise CodexAppServerError("Codex app-server is not running.")

        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(
        self,
        method: str,
        params: dict[str, Any],
        *,
        ensure_started: bool = True,
    ) -> None:
        if ensure_started:
            await self.start()
        await self._write({"method": method, "params": params})

    async def _write(self, payload: dict[str, Any]) -> None:
        process = self.process
        if not process or process.returncode is not None or process.stdin is None:
            raise CodexAppServerError("Codex app-server closed its stdio connection.")
        data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            process.stdin.write(data)
            await process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while line := await self.process.stdout.readline():
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Ignoring non-JSON Codex app-server output: %r", line[:300])
                    continue

                request_id = message.get("id")
                if request_id is not None and "method" not in message:
                    future = self._pending.get(request_id)
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(
                                CodexAppServerError(self._format_rpc_error(message["error"]))
                            )
                        else:
                            future.set_result(message.get("result"))
                    continue

                method = message.get("method")
                if request_id is not None and method:
                    await self._write(
                        {
                            "id": request_id,
                            "error": {
                                "code": -32601,
                                "message": "Artemis does not expose Codex-side tools or approvals.",
                            },
                        }
                    )
                    continue

                params = message.get("params") or {}
                thread_id = params.get("threadId")
                queue = self._thread_queues.get(thread_id)
                if queue is not None:
                    queue.put_nowait(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Codex app-server reader stopped: %s", exc, exc_info=True)
        finally:
            error = CodexAppServerError("Codex app-server exited before the request completed.")
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(error)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            while line := await self.process.stderr.readline():
                logger.debug("codex app-server: %s", line.decode(errors="replace").rstrip())
        except asyncio.CancelledError:
            raise

    @staticmethod
    def _format_rpc_error(error: Any) -> str:
        if isinstance(error, dict):
            message = error.get("message") or "Codex app-server request failed"
            data = error.get("data")
            return f"{message}: {data}" if data else str(message)
        return str(error)

    async def run_completion(
        self,
        *,
        model: str,
        effort: str | None,
        cwd: str,
        base_instructions: str,
        developer_instructions: str,
        inputs: list[dict[str, Any]],
        output_schema: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        await self.start()
        thread_params: dict[str, Any] = {
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "cwd": cwd,
            "ephemeral": True,
            "serviceName": "artemis",
            "baseInstructions": base_instructions,
            "developerInstructions": developer_instructions,
            "allowProviderModelFallback": False,
        }
        if model and model.lower() not in {"auto", "default"}:
            thread_params["model"] = model

        started = await self.request("thread/start", thread_params)
        thread = (started or {}).get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise CodexAppServerError("Codex app-server did not return a thread id.")

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._thread_queues[thread_id] = queue
        usage: dict[str, Any] | None = None
        try:
            turn_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": inputs,
                "outputSchema": output_schema,
            }
            if effort and effort != "none":
                turn_params["effort"] = effort
            await self.request("turn/start", turn_params)

            async def wait_for_turn() -> dict[str, Any]:
                nonlocal usage
                while True:
                    event = await queue.get()
                    method = event.get("method")
                    params = event.get("params") or {}
                    if method == "thread/tokenUsage/updated":
                        usage = params.get("tokenUsage")
                    if method == "turn/completed":
                        return params.get("turn") or {}

            turn = await asyncio.wait_for(wait_for_turn(), timeout=timeout_seconds)
        except TimeoutError as exc:
            raise CodexAppServerError(
                f"Codex model call timed out after {timeout_seconds:g} seconds."
            ) from exc
        finally:
            self._thread_queues.pop(thread_id, None)

        if turn.get("status") != "completed":
            error = turn.get("error") or turn.get("status") or "unknown error"
            raise CodexAppServerError(f"Codex turn failed: {error}")

        text_parts: list[str] = []
        for item in turn.get("items") or []:
            if item.get("type") == "agentMessage" and item.get("text"):
                text_parts.append(str(item["text"]))
        if not text_parts:
            raise CodexAppServerError("Codex turn completed without an assistant message.")
        return {
            "text": "\n".join(text_parts),
            "thread_id": thread_id,
            "model": (started or {}).get("model") or model,
            "usage": usage,
        }

    async def close(self) -> None:
        process = self.process
        self.process = None
        tasks = [task for task in (self._reader_task, self._stderr_task) if task is not None]
        self._reader_task = None
        self._stderr_task = None
        for task in tasks:
            if task and not task.done():
                task.cancel()
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def terminate(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()


_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, CodexAppServerClient] = (
    weakref.WeakKeyDictionary()
)


def _client_for_running_loop() -> CodexAppServerClient:
    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        binary = find_codex_binary()
        if not binary:
            raise CodexAppServerError(
                "Codex CLI was not found. Set ARTEMIS_CODEX_BIN or install the Codex client."
            )
        client = CodexAppServerClient(binary)
        _clients[loop] = client
    return client


@atexit.register
def _terminate_clients() -> None:
    for client in list(_clients.values()):
        client.terminate()


_IMAGE_SUFFIXES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

CODEX_IMAGE_MAX_EDGE = 1600
CODEX_IMAGE_MAX_BYTES = 768 * 1024
CODEX_IMAGE_JPEG_QUALITY = 82
CODEX_IMAGE_MIN_JPEG_QUALITY = 50


def _image_limit(name: str, default: int, minimum: int) -> int:
    """Read a positive image limit while keeping malformed env values harmless."""
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _rgb_image(image: Image.Image) -> Image.Image:
    """Flatten transparency onto white and return an RGB frame for JPEG output."""
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def _prepare_image_bytes(raw: bytes, suffix: str) -> tuple[bytes, str, bool]:
    """Downscale and compress an oversized model-bound screenshot.

    Small images pass through byte-for-byte. An image that exceeds either the
    configured edge or byte limit is converted to JPEG, capped by the longest
    edge, and then reduced in quality/size until it fits the byte budget.
    """
    max_edge = _image_limit("ARTEMIS_CODEX_IMAGE_MAX_EDGE", CODEX_IMAGE_MAX_EDGE, 320)
    max_bytes = _image_limit("ARTEMIS_CODEX_IMAGE_MAX_BYTES", CODEX_IMAGE_MAX_BYTES, 64 * 1024)
    start_quality = _image_limit("ARTEMIS_CODEX_IMAGE_JPEG_QUALITY", CODEX_IMAGE_JPEG_QUALITY, 50)
    start_quality = min(start_quality, 95)
    min_quality = min(CODEX_IMAGE_MIN_JPEG_QUALITY, start_quality)

    try:
        with Image.open(BytesIO(raw)) as source:
            source.seek(0)
            image = ImageOps.exif_transpose(source).copy()
    except (OSError, UnidentifiedImageError, ValueError):
        return raw, suffix, False

    original_size = image.size
    if max(original_size) <= max_edge and len(raw) <= max_bytes:
        return raw, suffix, False

    image = _rgb_image(image)
    if max(image.size) > max_edge:
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

    quality = start_quality
    encoded = raw
    while True:
        buffer = BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
        )
        encoded = buffer.getvalue()
        if len(encoded) <= max_bytes:
            break
        if quality > min_quality:
            quality = max(min_quality, quality - 8)
            continue
        if max(image.size) <= 480:
            break
        width, height = image.size
        scale = 0.85
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )

    logger.debug(
        "Prepared Codex image: "
        f"{original_size[0]}x{original_size[1]}/{len(raw)} bytes -> "
        f"{image.size[0]}x{image.size[1]}/{len(encoded)} bytes (quality={quality})"
    )
    return encoded, ".jpg", True


def _write_temp_image(raw: bytes, suffix: str) -> Path:
    temp_dir = Path(tempfile.gettempdir()) / "artemis-codex-images"
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / f"{uuid4().hex}{suffix}"
    path.write_bytes(raw)
    return path


def _materialize_data_image(url: str) -> Path:
    """Write a data-URL image to a temporary file for App Server.

    App Server's remote ``image`` input accepts a URL string, but a large data
    URL can leave the turn waiting indefinitely. Its native ``localImage``
    input is the reliable path for screenshots already resident on the host.
    """
    header, separator, payload = url.partition(",")
    if not separator or not header.lower().startswith("data:image/"):
        raise ValueError("Invalid image data URL")
    media_type = header[5:].split(";", 1)[0].lower()
    if ";base64" in header.lower():
        raw = base64.b64decode(payload)
    else:
        raw = unquote_to_bytes(payload)
    suffix = _IMAGE_SUFFIXES.get(media_type, ".img")
    prepared, prepared_suffix, _ = _prepare_image_bytes(raw, suffix)
    return _write_temp_image(prepared, prepared_suffix)


def _prepare_local_image(path: Path) -> tuple[Path, bool]:
    """Return a model-ready local image and whether the returned path is temporary."""
    try:
        raw = path.read_bytes()
    except OSError:
        return path, False
    prepared, suffix, changed = _prepare_image_bytes(raw, path.suffix or ".img")
    if not changed:
        return path, False
    return _write_temp_image(prepared, suffix), True


def _content_blocks(
    content: Any, *, materialize_images: bool = True
) -> tuple[list[str], list[dict[str, Any]], list[Path]]:
    """Convert LangChain text/image content into app-server turn inputs."""
    if isinstance(content, str):
        return ([content] if content else []), [], []
    if not isinstance(content, list):
        return [str(content)], [], []

    texts: list[str] = []
    images: list[dict[str, Any]] = []
    temp_paths: list[Path] = []
    for block in content:
        if isinstance(block, str):
            texts.append(block)
            continue
        if not isinstance(block, dict):
            texts.append(str(block))
            continue
        block_type = block.get("type")
        if block_type in {"text", "input_text"}:
            texts.append(str(block.get("text") or ""))
            continue
        if block_type in {"image_url", "input_image", "image"}:
            image_url = block.get("image_url") or block.get("url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            source = block.get("source")
            if not image_url and isinstance(source, dict) and source.get("data"):
                media_type = source.get("media_type") or "image/png"
                image_url = f"data:{media_type};base64,{source['data']}"
            if image_url and materialize_images:
                image_url = str(image_url)
                if image_url.lower().startswith("data:image/"):
                    path = _materialize_data_image(image_url)
                    temp_paths.append(path)
                    images.append({"type": "localImage", "path": str(path.resolve())})
                elif Path(image_url).is_file():
                    path, temporary = _prepare_local_image(Path(image_url).resolve())
                    if temporary:
                        temp_paths.append(path)
                    images.append({"type": "localImage", "path": str(path)})
                else:
                    images.append({"type": "image", "url": image_url})
                continue
        texts.append(json.dumps(block, ensure_ascii=False, default=str))
    return texts, images, temp_paths


def _message_transcript(
    messages: list[BaseMessage],
) -> tuple[str, list[dict[str, Any]], list[Path]]:
    sections: list[str] = []
    images: list[dict[str, Any]] = []
    temp_paths: list[Path] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            continue
        texts, message_images, message_temp_paths = _content_blocks(message.content)
        images.extend(message_images)
        temp_paths.extend(message_temp_paths)
        if isinstance(message, HumanMessage):
            role = "USER"
        elif isinstance(message, ToolMessage):
            role = f"TOOL RESULT ({message.name or message.tool_call_id})"
        elif isinstance(message, AIMessage):
            role = "ASSISTANT"
            if message.tool_calls:
                texts.append(
                    "Tool calls: " + json.dumps(message.tool_calls, ensure_ascii=False, default=str)
                )
        else:
            role = message.type.upper()
        sections.append(f"[{role}]\n" + "\n".join(texts))
    return "\n\n".join(sections), images, temp_paths


def _system_instructions(messages: list[BaseMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            texts, _, _ = _content_blocks(message.content, materialize_images=False)
            parts.extend(texts)
    return (
        "\n\n".join(parts) or "Follow the user's instructions and return only the requested result."
    )


def _tool_choice_name(tool_choice: Any) -> tuple[bool, str | None]:
    if tool_choice is True or tool_choice in ("any", "required"):
        return True, None
    if isinstance(tool_choice, str) and tool_choice not in {"auto", "none"}:
        return True, tool_choice
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function") or {}
        name = function.get("name") or tool_choice.get("name")
        return bool(name), name
    return False, None


def _response_contract(tools: list[dict[str, Any]], tool_choice: Any) -> tuple[dict[str, Any], str]:
    if not tools:
        schema = {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
            "additionalProperties": False,
        }
        return schema, "Return a JSON object with one string field named content."

    required, forced_name = _tool_choice_name(tool_choice)
    available = [tool["function"]["name"] for tool in tools]
    if forced_name:
        if forced_name not in available:
            raise ValueError(f"Unknown forced tool {forced_name!r}; available tools: {available}")
        allowed_names = [forced_name]
    else:
        allowed_names = available
    kinds = ["tool_call"] if required else ["tool_call", "final"]
    schema = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": kinds},
            "content": {"type": "string"},
            "tool_name": {"type": "string", "enum": allowed_names},
            "tool_arguments_json": {"type": "string"},
        },
        "required": ["kind", "content", "tool_name", "tool_arguments_json"],
        "additionalProperties": False,
    }
    contract = (
        "Artemis owns tool execution. Do not run shell commands, browse, edit files, or use any "
        "Codex built-in tools. Choose from the supplied Artemis tool schemas. Return exactly the "
        "JSON object required by the output schema. For kind=tool_call, set tool_name and encode "
        "the arguments object as JSON in tool_arguments_json; content may be empty. For kind=final, "
        "put the answer in content and use the first available tool name with '{}' arguments."
    )
    return schema, contract


def _parse_response(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines)
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else {"content": str(value)}
    except json.JSONDecodeError:
        return {"content": text}


class CodexAppServerChatModel(BaseChatModel):
    """A LangChain chat model backed by the signed-in Codex desktop/CLI client."""

    model_name: str = Field(default="default")
    reasoning_effort: str | None = Field(default=None)
    timeout_seconds: float = Field(default=180.0, gt=0)
    cwd: str = Field(default_factory=os.getcwd)

    @property
    def _llm_type(self) -> str:
        return "codex-app-server"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "reasoning_effort": self.reasoning_effort,
            "transport": "app-server-stdio",
        }

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ):
        formatted = [convert_to_openai_tool(tool) for tool in tools]
        return self.bind(tools=formatted, tool_choice=tool_choice, **kwargs)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        tools = list(kwargs.get("tools") or [])
        tool_choice = kwargs.get("tool_choice")
        output_schema, contract = _response_contract(tools, tool_choice)
        transcript, images, temp_paths = _message_transcript(messages)
        inputs: list[dict[str, Any]] = [
            {"type": "text", "text": transcript or "Continue from the supplied instructions."}
        ]
        inputs.extend(images)
        developer_parts = [contract]
        if tools:
            developer_parts.append(
                "Available Artemis tools:\n" + json.dumps(tools, ensure_ascii=False, default=str)
            )
        try:
            result = await _client_for_running_loop().run_completion(
                model=self.model_name,
                effort=self.reasoning_effort,
                cwd=str(Path(self.cwd).resolve()),
                base_instructions=_system_instructions(messages),
                developer_instructions="\n\n".join(developer_parts),
                inputs=inputs,
                output_schema=output_schema,
                timeout_seconds=self.timeout_seconds,
            )
        finally:
            for path in temp_paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.debug("Could not remove temporary Codex image %s", path)
        parsed = _parse_response(result["text"])
        response_metadata = {
            "provider": "codex",
            "model": result.get("model") or self.model_name,
            "thread_id": result.get("thread_id"),
        }
        usage_metadata = None
        last_usage = (result.get("usage") or {}).get("last") or {}
        if last_usage:
            usage_metadata = {
                "input_tokens": int(last_usage.get("inputTokens") or 0),
                "output_tokens": int(last_usage.get("outputTokens") or 0),
                "total_tokens": int(last_usage.get("totalTokens") or 0),
            }

        if tools and parsed.get("kind") == "tool_call":
            name = parsed.get("tool_name")
            raw_args = parsed.get("tool_arguments_json") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError as exc:
                    raise CodexAppServerError(
                        f"Codex returned invalid JSON arguments for tool {name!r}: {raw_args}"
                    ) from exc
            else:
                args = raw_args
            message = AIMessage(
                content=parsed.get("content") or "",
                tool_calls=[
                    {
                        "name": str(name),
                        "args": args,
                        "id": f"call_{uuid4().hex}",
                        "type": "tool_call",
                    }
                ],
                response_metadata=response_metadata,
                usage_metadata=usage_metadata,
            )
        else:
            message = AIMessage(
                content=str(parsed.get("content") or result["text"]),
                response_metadata=response_metadata,
                usage_metadata=usage_metadata,
            )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del run_manager

        async def invoke_and_close() -> ChatResult:
            loop = asyncio.get_running_loop()
            try:
                return await self._agenerate(messages, stop=stop, **kwargs)
            finally:
                client = _clients.pop(loop, None)
                if client is not None:
                    await client.close()

        return asyncio.run(invoke_and_close())
