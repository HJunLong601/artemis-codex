"""Local console model routing editor; credentials never appear in the model DTO."""

import asyncio
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from apps.admin_console.routers.codex_auth import require_local
from apps.admin_console.services import model_settings as service

router = APIRouter(prefix="/api/system/model-settings", tags=["system"])


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=100)
    value: Any = None
    remove: bool = False


class Save(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=r"^[0-9a-f]{64}$")
    edits: list[Edit] = Field(max_length=500)


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    api_key: SecretStr


def boundary(request, mutation=False):
    require_local(request)
    if mutation and request.headers.get("x-artemis-config-intent") != "save":
        raise HTTPException(403, "Explicit model configuration intent is required.")


@router.get("")
def read(request: Request):
    boundary(request)
    try:
        return service.snapshot()
    except service.SettingsError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except OSError:
        raise HTTPException(503, "无法读取本地模型配置。") from None


@router.get("/codex-models")
async def codex_models(request: Request, refresh: bool = False):
    boundary(request)
    from artemis.llm.codex_app_server import CodexAppServerClient, find_codex_binary
    from artemis.llm.model_preflight import _catalog

    binary = find_codex_binary()
    if not binary:
        raise HTTPException(503, "未找到 Codex 客户端；仍可手动填写模型名称。")
    client = CodexAppServerClient(binary)
    try:
        rows = await asyncio.wait_for(_catalog(client, force=refresh), timeout=20)
        models = {}
        for row in rows:
            model = row.get("model")
            if row.get("hidden") or not isinstance(model, str) or not model or len(model) > 200:
                continue
            display = row.get("displayName")
            models[model] = {
                "model": model,
                "display_name": display[:160] if isinstance(display, str) else model,
                "is_default": row.get("isDefault") is True,
            }
        return {"models": list(models.values()), "inference_tested": False}
    except (OSError, RuntimeError, TimeoutError, ValueError, TypeError, AttributeError):
        raise HTTPException(
            503, "无法读取 Codex 模型列表；请检查客户端与登录状态，或手动填写。"
        ) from None
    finally:
        try:
            await asyncio.wait_for(client.close(), timeout=5)
        except (OSError, RuntimeError, TimeoutError):
            pass


@router.put("")
def save(request: Request, payload: Save):
    boundary(request, True)
    try:
        result = service.save(payload.version, payload.edits)
    except service.SettingsError as exc:
        raise HTTPException(exc.status, str(exc)) from None
    except OSError:
        raise HTTPException(503, "无法保存配置，请检查文件写入权限；请重新加载确认。") from None
    from artemis.core.diagnostics import readiness_engine
    from apps.admin_console.services.model_service import model_service

    readiness_engine.invalidate_cache()
    model_service._llm_info_cache = None
    return {**result, "saved": True}


@router.post("/credentials")
async def credentials(request: Request, payload: Credential):
    boundary(request, True)
    if payload.provider not in {"google", "openai", "anthropic", "openrouter", "xai", "ocr"}:
        raise HTTPException(422, "不支持该凭证提供商。")
    key = payload.api_key.get_secret_value().strip()
    if not key or len(key) > 8192 or any(ord(char) < 32 for char in key):
        raise HTTPException(422, "请输入有效的 API Key。")
    from artemis.utils.credentials_validator import validate_api_key
    from artemis.core.diagnostics import readiness_engine

    try:
        valid, _ = await validate_api_key(provider=payload.provider, api_key=key)
        if not valid:
            raise ValueError("Credential verification failed")
        await asyncio.to_thread(service.persist_key, payload.provider, key)
    except Exception:
        raise HTTPException(400, "凭证验证或保存失败，请检查提供商、网络和文件权限。") from None
    readiness_engine.invalidate_cache()
    return {"saved": True, "provider": payload.provider}
