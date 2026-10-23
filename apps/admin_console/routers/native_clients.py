"""Explicit local switch between independent Codex and Claude Code profiles."""

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from apps.admin_console.routers.codex_auth import require_local
from artemis.llm.claude_code_setup import native_client_settings, select_native_client

router = APIRouter(prefix="/model-clients", tags=["system"])


class SelectionRequest(BaseModel):
    provider: Literal["codex", "claude_code"]


@router.get("")
async def status(request: Request):
    require_local(request, client_name="Model client")
    try:
        return await asyncio.to_thread(native_client_settings)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError):
        raise HTTPException(
            409, "Model client profiles could not be read. Check the local configuration."
        ) from None


@router.post("/select")
async def select(request: Request, body: SelectionRequest):
    require_local(request, mutation=True, client_name="Model client")
    try:
        result = await asyncio.to_thread(select_native_client, body.provider)
        from apps.admin_console.services.model_service import ModelService

        ModelService._llm_info_cache = None
        return result
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(
            409, "Model client selection was not saved. Existing profiles and backups are retained."
        ) from None
