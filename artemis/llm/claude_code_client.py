"""Inference-only use of the unmodified, user-authenticated Claude Code CLI.

No credential-file access, HTTP proxy protocol, embedded OAuth or nested MCP.
Every completion has its own private working directory and conversation.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import tempfile
import threading
import time
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, ValidationError


class ClaudeCodeError(RuntimeError):
    """Public-safe error understood by Artemis's shared retry/fallback gateway."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def find_claude_code_binary(configured: str | None = None) -> str | None:
    override = configured or os.getenv("ARTEMIS_CLAUDE_CODE_BIN")
    if override:
        candidate = shutil.which(override)
        return str(Path(candidate).absolute()) if candidate else None
    candidate = shutil.which("claude") or shutil.which("claude.exe")
    if candidate:
        return str(Path(candidate).absolute())
    for name in ("claude", "claude.exe"):
        candidate = Path.home() / ".local" / "bin" / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    # The child owns an isolated session, not its caller's interactive session.
    # Never alter authentication/provider variables or user configuration files.
    environment.pop("CLAUDECODE", None)
    environment.pop("CLAUDE_CODE_SESSION_ID", None)
    return environment


async def _read_bounded(stream, limit: int) -> bytes:
    parts = []
    size = 0
    while chunk := await stream.read(65536):
        size += len(chunk)
        if size > limit:
            raise ClaudeCodeError("Claude Code output exceeded the safe response limit.")
        parts.append(chunk)
    return b"".join(parts)


async def _stop_process(process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def _run(
    command: list[str], *, timeout: float, data: bytes = b"", cwd: str | None = None
) -> tuple[int, bytes, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=_environment(),
            start_new_session=os.name != "nt",
        )
    except OSError:
        raise ClaudeCodeError(
            "Claude Code could not start; check the executable and runtime.", 503
        ) from None

    async def send():
        try:
            process.stdin.write(data)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # An early CLI error is classified from its bounded output.
        finally:
            process.stdin.close()

    tasks = [
        asyncio.create_task(_read_bounded(process.stdout, 16 * 1024 * 1024)),
        asyncio.create_task(_read_bounded(process.stderr, 256 * 1024)),
        asyncio.create_task(send()),
        asyncio.create_task(process.wait()),
    ]
    try:
        output = await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
        return process.returncode, output[0], output[1]
    finally:
        await _stop_process(process)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


_capabilities: dict[tuple[str, int, int], tuple[float, str]] = {}
_REQUIRED_FLAGS = (
    "--safe-mode",
    "--tools",
    "--strict-mcp-config",
    "--input-format",
    "--output-format",
    "--json-schema",
    "--no-session-persistence",
)


async def _version(binary: str) -> str:
    stat = Path(binary).stat()
    key = (str(Path(binary).resolve()), stat.st_mtime_ns, stat.st_size)
    cached = _capabilities.get(key)
    if cached and time.monotonic() - cached[0] < 600:
        return cached[1]
    version, help_result = await asyncio.gather(
        _run([binary, "--version"], timeout=10), _run([binary, "--help"], timeout=10)
    )
    match = re.search(rb"\b(\d+\.\d+\.\d+)\b", version[1])
    if (
        version[0]
        or help_result[0]
        or not match
        or any(flag.encode() not in help_result[1] for flag in _REQUIRED_FLAGS)
    ):
        raise ClaudeCodeError(
            "Claude Code lacks required inference isolation flags; update the official CLI.", 400
        )
    value = match[1].decode("ascii")
    if len(_capabilities) >= 16:
        _capabilities.clear()
    _capabilities[key] = (time.monotonic(), value)
    return value


async def probe_claude_code(binary: str | None = None) -> dict[str, Any]:
    """Inspect only official CLI facts, without returning account data or errors."""
    resolved = find_claude_code_binary(binary)
    report = {"state": "binary_missing", "binary": resolved, "inference_tested": False}
    if not resolved:
        return report
    try:
        report["version"] = await _version(resolved)
        code, stdout, _ = await _run([resolved, "auth", "status"], timeout=15)
        result = json.loads(stdout)
        if not isinstance(result, dict) or not isinstance(result.get("loggedIn"), bool):
            raise ValueError("Invalid authentication response")
        if code not in (0, 1) or (result["loggedIn"] and code != 0):
            raise ValueError("Unconfirmed authentication response")
        report["state"] = "authenticated" if result["loggedIn"] else "not_logged_in"
        method = result.get("authMethod")
        report["auth_method"] = (
            method
            if method
            in {"none", "claude.ai", "oauth_token", "api_key", "api_key_helper", "third_party"}
            else "unknown"
        )
    except ClaudeCodeError as exc:
        report["state"] = (
            "unsupported_client" if exc.status_code == 400 else "credential_check_failed"
        )
    except (OSError, RuntimeError, ValueError, TimeoutError):
        report["state"] = "credential_check_failed"
    return report


_auth_cache: dict[tuple, tuple[float, tuple[bool, str]]] = {}
_auth_lock = threading.RLock()


def clear_auth_cache() -> None:
    with _auth_lock:
        _auth_cache.clear()


def claude_code_status(binary: str | None = None) -> tuple[bool, str]:
    """The shared sync readiness gate; failed authentication is never cached."""
    resolved = find_claude_code_binary(binary)
    identity = hashlib.sha256(
        json.dumps(
            {
                name: os.getenv(name)
                for name in (
                    "CLAUDE_CONFIG_DIR",
                    "ANTHROPIC_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN",
                    "CLAUDE_CODE_OAUTH_TOKEN",
                    "CLAUDE_CODE_USE_BEDROCK",
                    "CLAUDE_CODE_USE_VERTEX",
                    "CLAUDE_CODE_USE_FOUNDRY",
                )
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    key = (resolved, identity)
    with _auth_lock:
        cached = _auth_cache.get(key)
        if cached and time.monotonic() - cached[0] < 3:
            return cached[1]
        with ThreadPoolExecutor(max_workers=1) as executor:
            report = executor.submit(lambda: asyncio.run(probe_claude_code(binary))).result()
        messages = {
            "authenticated": "Claude Code is connected; model inference was not tested.",
            "binary_missing": "Claude Code is not installed. Install the official CLI and run claude auth login.",
            "not_logged_in": "Claude Code is not logged in. Run claude auth login in the Artemis runtime environment.",
            "unsupported_client": "Claude Code lacks required isolation support. Update the official CLI.",
            "credential_check_failed": "Claude Code authentication could not be checked; inspect the local console diagnostics.",
        }
        result = (report["state"] == "authenticated", messages[report["state"]])
        if result[0]:
            if len(_auth_cache) >= 16:
                _auth_cache.clear()
            _auth_cache[key] = (time.monotonic(), result)
        else:
            _auth_cache.pop(key, None)
        return result


claude_code_status.cache_clear = clear_auth_cache


def _execution_error(text: str) -> ClaudeCodeError:
    # Inspect in memory, but never return provider text containing user input.
    lowered = text.lower()
    if any(
        part in lowered
        for part in (
            "not logged",
            "authentication",
            "unauthorized",
            "invalid api key",
            "login required",
            "token expired",
        )
    ):
        return ClaudeCodeError(
            "Claude Code authentication failed. Run claude auth login and recheck the runtime.", 401
        )
    if any(
        part in lowered
        for part in (
            "rate limit",
            "usage limit",
            "quota",
            "too many requests",
            "hit your limit",
            "credit balance is too low",
        )
    ):
        return ClaudeCodeError(
            "Claude Code rate limit reached; retry later or use an authorized fallback.", 429
        )
    if any(
        part in lowered
        for part in (
            "unknown option",
            "invalid model",
            "model is not",
            "invalid schema",
            "not a valid json schema",
        )
    ):
        return ClaudeCodeError("Claude Code rejected the model, schema or CLI configuration.", 400)
    return ClaudeCodeError("Claude Code failed to produce a valid inference result.")


async def run_claude_completion(
    *,
    binary: str | None = None,
    model: str,
    effort: str | None = None,
    system_prompt: str,
    content: list[dict],
    output_schema: dict,
    timeout: float = 90,
) -> dict[str, Any]:
    """Send one independent inference, with no executable host tools attached."""
    resolved = find_claude_code_binary(binary)
    if not resolved:
        raise ClaudeCodeError("Claude Code is not installed; install the official CLI.", 401)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", model):
        raise ClaudeCodeError("Invalid Claude Code model identifier.", 400)
    if effort not in {None, "none", "low", "medium", "high", "xhigh", "max"}:
        raise ClaudeCodeError("Unsupported Claude Code reasoning effort.", 400)
    if not 0 < timeout <= 1800:
        raise ClaudeCodeError("Invalid Claude Code inference timeout.", 400)
    try:
        Draft202012Validator.check_schema(output_schema)
    except SchemaError:
        raise ClaudeCodeError("Invalid Claude Code output schema.", 400) from None
    await _version(resolved)
    schema_json = json.dumps(output_schema, ensure_ascii=True)
    if len(schema_json.encode()) > 64 * 1024:
        raise ClaudeCodeError("Claude Code output schema exceeds the safe argument limit.", 400)
    with tempfile.TemporaryDirectory(prefix="artemis-claude-") as directory:
        prompt = Path(directory) / "system.txt"
        prompt.touch(mode=0o600)
        prompt.write_text(system_prompt, encoding="utf-8")
        command = [
            resolved,
            "-p",
            "--safe-mode",
            "--tools",
            "",
            "--disallowedTools",
            "mcp__*",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--no-session-persistence",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--system-prompt-file",
            str(prompt),
            "--json-schema",
            schema_json,
        ]
        if model not in {"auto", "default"}:
            command.extend(["--model", model])
        if effort not in {None, "none"}:
            command.extend(["--effort", effort])
        payload = {"type": "user", "message": {"role": "user", "content": content}}
        code, stdout, stderr = await _run(
            command,
            data=(json.dumps(payload, ensure_ascii=False) + "\n").encode(),
            cwd=directory,
            timeout=timeout,
        )
    result = None
    try:
        for line in stdout.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("Invalid event")
            if event.get("type") == "result":
                result = event
    except (ValueError, UnicodeError):
        raise _execution_error(stderr.decode(errors="replace")) from None
    if (
        code
        or not result
        or result.get("is_error")
        or str(result.get("subtype", "")).startswith("error")
    ):
        raise _execution_error(
            str((result or {}).get("result", "")) + stderr.decode(errors="replace")
        )
    structured = result.get("structured_output")
    try:
        Draft202012Validator(output_schema).validate(structured)
    except ValidationError:
        raise ClaudeCodeError("Claude Code returned an invalid structured response.") from None
    return result
