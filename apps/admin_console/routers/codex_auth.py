"""Loopback-only authentication controls, with explicit browser intent."""

import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from apps.admin_console.services.codex_auth_service import codex_auth_service, LoginFlowError

router = APIRouter(prefix="/codex/auth", tags=["system"])


def require_local(request: Request, *, mutation=False, client_name="Codex"):
    try:
        peer = request.client.host if request.client else ""
        host = request.url.hostname or ""
        local = peer == "localhost" or ipaddress.ip_address(peer).is_loopback
        local_host = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = local_host = False
    origin = request.headers.get("origin")
    if (
        not local
        or not local_host
        or (origin and urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower())
    ):
        raise HTTPException(
            403, f"{client_name} authentication requires the local same-origin console."
        )
    if mutation and request.headers.get("x-artemis-auth-intent") != "login":
        raise HTTPException(403, f"Explicit {client_name} authentication intent is required.")


@router.get("")
async def status(request: Request):
    require_local(request)
    try:
        return await codex_auth_service.status()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return {
            "state": "credential_check_failed",
            "credential_access": "failed",
            "inference_tested": False,
        }


@router.post("/start")
async def start(request: Request):
    require_local(request, mutation=True)
    try:
        return await codex_auth_service.start()
    except LoginFlowError as exc:
        raise HTTPException(409, str(exc)) from None
    except (OSError, RuntimeError, TimeoutError, ValueError):
        raise HTTPException(
            503, "Codex login could not start. Check the runtime and credential store."
        ) from None


@router.post("/cancel")
async def cancel(request: Request):
    require_local(request, mutation=True)
    try:
        return await codex_auth_service.cancel()
    except (OSError, RuntimeError, TimeoutError, ValueError):
        raise HTTPException(
            503, "Codex login cancellation failed; the local login process was closed."
        ) from None


@router.post("/verify")
async def verify(request: Request):
    require_local(request, mutation=True)
    from artemis.config.llm import parse_llm_config
    from artemis.llm.model_preflight import preflight_models

    config = parse_llm_config()
    if not any(model.provider == "codex" for _, model in config.iter_primary_models()):
        raise HTTPException(
            409, "Select Codex before testing Codex inference. Claude Code is independent."
        )
    report = await codex_auth_service.status()
    if report["state"] not in {"authenticated", "not_required"}:
        raise HTTPException(
            409, "Sign in to Codex in the Artemis runtime before testing inference."
        )
    result = await preflight_models(config, verify_inference=True, force=True)
    return {
        "ok": result["ok"],
        "inference_tested": result.get("inference_tested", False),
        "checks": result.get("checks", []),
    }
