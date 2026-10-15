"""Prefer the desktop client's bundled Codex during automatic discovery."""

from functools import lru_cache
import os
from pathlib import Path
import sys

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
    if sys.platform == "win32" and os.getenv("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "Programs/OpenAI/Codex/bin/codex.exe",)
    return ()


def find_codex_binary(configured: str | None = None) -> str | None:
    """Honor explicit overrides, then desktop installs, then the provider's CLI fallback."""
    override = configured or os.getenv("CODEX_CLIENT_BIN") or os.getenv("ARTEMIS_CODEX_BIN")
    if override and override.strip():
        return provider.find_codex_binary(override)
    for candidate in _desktop_candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return provider.find_codex_binary()


@lru_cache(maxsize=1)
def codex_client_status(binary: str | None = None) -> tuple[bool, str]:
    """Use the same executable for login diagnostics, preflight and inference."""
    resolved = find_codex_binary(binary)
    if not resolved:
        return False, "Codex was not found. Install/open Codex and sign in first."
    # The public cache_clear() must also refresh the pinned provider's login cache.
    provider.codex_client_status.cache_clear()
    return provider.codex_client_status(resolved)
