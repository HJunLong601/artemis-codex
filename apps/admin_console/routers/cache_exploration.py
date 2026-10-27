"""Explicit, loopback-only safe exploration controls and read-only reports."""

import asyncio
import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from apps.admin_console.routers.codex_auth import require_local
from apps.admin_console.services.cache_exploration import manager
from artemis.context import ArtemisContext, DeviceContext, DevicePlatform
from artemis.runtime import DeviceExecutionLock, device_registry
from artemis.runtime.cache_exploration.model import routes
from artemis.runtime.cache_exploration.store import Conflict
from artemis.runtime.device_registry import normalize_device_request
from artemis.runtime.adb_endpoint import current_adb_endpoint
from artemis.utils.visual_location_cache import cache_config_for_context

router = APIRouter(prefix="/api/system/visual-cache/exploration", tags=["system"])


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=8, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    device_serial: str = Field(min_length=1, max_length=256, pattern=r"^\S+$")
    package: str = Field(
        min_length=3, max_length=255, pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+$"
    )
    duration_minutes: StrictInt = Field(ge=1, le=1440)
    max_steps: StrictInt | None = Field(default=None, ge=1, le=20000)
    max_tokens: StrictInt | None = Field(default=None, ge=1, le=10000000)
    confirm_model_call: Literal[True]
    route_snapshot: dict

    @field_validator("confirm_model_call", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit boolean confirmation is required")
        return value


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: StrictInt | None = None


def local(request, mutation=False):
    require_local(request)
    if mutation and request.headers.get("x-artemis-cache-intent") != "exploration":
        raise HTTPException(403, "Explicit exploration intent is required.")


def get(task_id):
    try:
        return manager.reconcile(manager.store().get(task_id))
    except KeyError:
        raise HTTPException(404, "Exploration task not found.") from None


@router.get("/capabilities")
async def capabilities(request: Request):
    local(request)
    try:
        configured = routes()
    except Exception:
        raise HTTPException(503, "Model configuration is unavailable; check Setup Guide.") from None
    return {
        "platform": "android",
        "routes": configured,
        "enabled": cache_config_for_context().enabled
        and os.getenv("ARTEMIS_VISUAL_LOCATION_CACHE", "1") != "0",
        "limits": {"duration_minutes": 1440, "max_steps": 20000, "max_tokens": 10000000},
        "unlimited": {"max_steps": True, "max_tokens": True, "duration_minutes": False},
    }


@router.get("/apps")
async def apps(request: Request, device_serial: str = Query(min_length=1, max_length=256)):
    local(request)
    try:
        device = await device_registry.select_device_async(DevicePlatform.ANDROID, device_serial)
        if DeviceExecutionLock.get_active_owner(device.canonical_id):
            raise HTTPException(409, "Device is busy; pause its task before reading apps.")
        ctx = ArtemisContext(
            device=DeviceContext(
                mobile_platform=device.platform,
                device_id=device.device_id,
                device_name=device.name,
                device_kind=device.kind.value,
            )
        )
        from third_party.mobile_use.controllers.platform_specific_commands_controller import (
            list_packages,
        )

        # The legacy package reader requires an initialized ADB client in its context.
        # Driver construction binds that client without touching or navigating the screen.
        from artemis.drivers.factory import get_driver

        get_driver(ctx)
        packages = await asyncio.wait_for(asyncio.to_thread(list_packages, ctx), 15)
        return {"items": [{"package": p, "name": p} for p in packages.splitlines() if p][:2000]}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            503, "Cannot read apps from the selected authorized Android device."
        ) from None


@router.post("/tasks")
async def start(request: Request, body: Start):
    local(request, True)
    config = body.model_dump(exclude={"request_key", "confirm_model_call", "route_snapshot"})
    try:
        platform, serial = normalize_device_request(DevicePlatform.ANDROID, body.device_serial)
    except ValueError:
        raise HTTPException(422, "Select an explicit Android device.") from None
    if platform != DevicePlatform.ANDROID or not serial:
        raise HTTPException(422, "Select an explicit Android device.")
    config["device_serial"] = f"android:{serial}"
    config["routes"] = body.route_snapshot
    existing = manager.store().by_request_key(body.request_key)
    if existing:
        previous = {k: v for k, v in existing["config"].items() if k != "adb_endpoint"}
        if previous != config:
            raise HTTPException(409, "idempotency_conflict")
        return get(existing["id"])
    if (
        not cache_config_for_context().enabled
        or os.getenv("ARTEMIS_VISUAL_LOCATION_CACHE", "1") == "0"
    ):
        raise HTTPException(409, "Enable visual caching before exploration.")
    try:
        try:
            configured = routes()
        except Exception:
            raise HTTPException(
                503, "Model configuration is unavailable; check Setup Guide."
            ) from None
        if configured != body.route_snapshot:
            raise HTTPException(409, "Model configuration changed; review and confirm again.")
        config["routes"] = configured
        config["adb_endpoint"] = current_adb_endpoint().to_dict()
        task, created = manager.store().create(body.request_key, config)
        if created:
            await manager.spawn(task["id"])
        return get(task["id"])
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/tasks")
async def tasks(
    request: Request, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)
):
    local(request)
    return {"items": [manager.reconcile(task) for task in manager.store().list(limit, offset)]}


@router.get("/tasks/{task_id}")
async def task(request: Request, task_id: str):
    local(request)
    return get(task_id)


@router.post("/tasks/{task_id}/{action}")
async def control(
    request: Request, task_id: str, action: Literal["pause", "resume", "stop"], body: Control
):
    local(request, True)
    get(task_id)
    try:
        before = manager.store().get(task_id)
        updated = manager.store().control(task_id, action, body.version)
        manager.watch_control(updated)
        if action == "resume" and before["status"] != "resuming":
            await manager.spawn(task_id)
        elif action == "stop" and not updated["pid"] and task_id not in manager.children:
            manager.store().finish(task_id, "stopped", "user_stop")
        return get(task_id)
    except Conflict as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/tasks/{task_id}/pages")
async def pages(
    request: Request,
    task_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    local(request)
    get(task_id)
    return {"items": manager.store().pages(task_id, limit, offset)}


@router.get("/tasks/{task_id}/report")
async def report(request: Request, task_id: str):
    local(request)
    return {
        "task": get(task_id),
        "pages": manager.store().pages(task_id, 2000),
        "scope": "Only observed pages; not whole-app coverage. Unsettled usage is never zero.",
    }
