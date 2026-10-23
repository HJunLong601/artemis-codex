"""Local native-client diagnostics; authentication stays in the official CLI."""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Literal

from apps.admin_console.routers.codex_auth import require_local
from artemis.config.llm import parse_llm_config
from artemis.llm.claude_code_client import probe_claude_code
from artemis.llm.claude_code_preflight import preflight_claude_models

router = APIRouter(prefix="/claude-code/auth", tags=["system"])
_verify_lock = asyncio.Lock()


class ConfigureRequest(BaseModel):
    confirm_save_claude_settings: Literal[True]
    model: str = Field(default="sonnet", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
    fallback: str = Field(default="haiku", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"


@router.post("/configure")
async def configure(request: Request, body: ConfigureRequest):
    require_local(request, mutation=True, client_name="Claude Code")
    from artemis.llm.claude_code_setup import configure_claude_code

    try:
        return await asyncio.to_thread(
            configure_claude_code, body.model, body.fallback, body.effort
        )
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(
            409,
            "Claude Code configuration could not be saved. Check the writable unified config; existing configuration and backups are retained.",
        ) from None


@router.get("")
async def status(request: Request):
    require_local(request, client_name="Claude Code")
    report = await probe_claude_code()
    report["login_command"] = "claude auth login"
    report["status_command"] = "claude auth status"
    report["setup_url"] = "https://code.claude.com/docs/en/overview"
    try:
        config = parse_llm_config()
        report["required"] = any(
            model.provider == "claude_code" for _, model in config.iter_primary_models()
        )
        report["configured_models"] = sorted(
            {
                model.model
                for _, model in config.iter_primary_models()
                if model.provider == "claude_code"
            }
        )
        from artemis.llm.claude_code_setup import native_client_settings

        report["model_settings"] = next(
            item
            for item in native_client_settings()["clients"]
            if item["provider"] == "claude_code"
        )
    except (OSError, RuntimeError, ValueError):
        report["routing_error"] = "Model configuration could not be read."
    return report


@router.post("/verify")
async def verify(request: Request):
    require_local(request, mutation=True, client_name="Claude Code")
    if _verify_lock.locked():
        raise HTTPException(409, "Claude Code inference verification is already running.")
    async with _verify_lock:
        try:
            result = await asyncio.wait_for(
                preflight_claude_models(parse_llm_config(), verify_inference=True), timeout=90
            )
        except TimeoutError:
            raise HTTPException(504, "Claude Code inference verification timed out.") from None
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(
                503, "Claude Code verification could not complete; check local diagnostics."
            ) from None
        if not result["checks"]:
            raise HTTPException(
                409, "Configure a claude_code model route before testing inference."
            )
        return result
