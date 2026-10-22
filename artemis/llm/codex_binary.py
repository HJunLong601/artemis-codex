"""Prefer the desktop client's bundled Codex during automatic discovery."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import logging
import shutil
from pathlib import Path
import sys
import threading
import time

import codex_client_provider.langchain as provider


def _desktop_candidates() -> tuple[Path, ...]:
    if sys.platform == "darwin":
        bundles = (
            "Codex.app/Contents/Resources/codex",
            "ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
        )
        return tuple(
            root / bundle
            for root in (Path("/Applications"), Path.home() / "Applications")
            for bundle in bundles
        )
    if sys.platform == "win32":
        # MCP launch environments can omit LOCALAPPDATA; the signed-in user's
        # standard local application directory is still discoverable.
        local = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData/Local")
        bundles = []
        for path in (local / "OpenAI/Codex/bin").glob("*/codex.exe"):
            try:
                bundles.append((path.stat().st_mtime, path))
            except OSError:
                continue  # A desktop update may remove a version during discovery.
        bundles = [path for _, path in sorted(bundles, key=lambda item: item[0], reverse=True)]
        return (
            *bundles,
            local / "OpenAI/Codex/bin/codex.exe",
            local / "Programs/OpenAI/Codex/bin/codex.exe",
        )
    return ()


def find_codex_binary(configured: str | None = None) -> str | None:
    """Honor explicit overrides, then desktop installs, then the provider's CLI fallback."""
    if configured and configured.strip():
        return provider.find_codex_binary(configured)
    override = os.getenv("CODEX_CLIENT_BIN") or os.getenv("ARTEMIS_CODEX_BIN")
    if override and override.strip():
        resolved = provider.find_codex_binary(override)
        if resolved:
            return resolved
        logging.getLogger(__name__).warning(
            "Configured Codex environment path no longer exists; rediscovering installed desktop client"
        )
    for candidate in _desktop_candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    if override and override.strip():
        return shutil.which("codex.exe") or shutil.which("codex")
    return provider.find_codex_binary()


_auth_cache: dict[tuple[str, str, str], tuple[float, tuple[bool, str]]] = {}
_auth_lock = threading.RLock()
_AUTH_CACHE_SECONDS = 3.0


def _clear_auth_cache() -> None:
    with _auth_lock:
        _auth_cache.clear()


def codex_client_status(binary: str | None = None) -> tuple[bool, str]:
    """Shared sync gate for CLI, SDK, MCP and readiness; no second CLI verdict.

    Positive results have a short TTL; negative results are never cached so an
    external login is visible immediately. Actual task preflight is always fresh.
    """
    resolved = find_codex_binary(binary)
    if not resolved:
        return False, "Codex was not found. Install/open Codex and sign in first."
    key = (resolved, os.getenv("CODEX_HOME") or str(Path.home() / ".codex"), str(Path.cwd()))
    from artemis.llm.codex_auth import inspect_runtime_account

    with _auth_lock:
        cached = _auth_cache.get(key)
        if cached and time.monotonic() - cached[0] < _AUTH_CACHE_SECONDS:
            return cached[1]
        try:
            # Configuration validation is synchronous and can run inside an
            # active event loop. A separate loop avoids nested asyncio.run().
            with ThreadPoolExecutor(max_workers=1) as executor:
                report = executor.submit(
                    lambda: asyncio.run(inspect_runtime_account(resolved))
                ).result()
        except (OSError, RuntimeError, TimeoutError, ValueError):
            return (
                False,
                "Codex authentication check failed. Open the local console authentication diagnostics.",
            )
        if report["state"] in {"authenticated", "not_required"}:
            result = (True, "Codex App Server is connected; model inference was not tested.")
            if len(_auth_cache) >= 16:
                _auth_cache.clear()
            _auth_cache[key] = (time.monotonic(), result)
            return result
        _auth_cache.pop(key, None)
        return False, "Codex is not logged in. Open the local console Codex login."


# Keep the public invalidation contract used by diagnostics and login lifecycle.
codex_client_status.cache_clear = _clear_auth_cache
