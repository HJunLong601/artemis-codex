"""本机控制台的 Android App 源码组管理接口。"""

import asyncio
from collections.abc import Iterator
from contextlib import closing, contextmanager
import sqlite3
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from apps.admin_console.routers.codex_auth import require_local
from apps.admin_console.routers.visual_cache_details import read_source_locating
from apps.admin_console.services.app_project_comparison import compare
from artemis.config import app_projects as service
from artemis.utils.visual_location_cache import configured_cache

# 和其他本机控制台配置使用相同的系统 API 前缀。
router = APIRouter(prefix="/api/system/app-projects", tags=["system"])


class CompareRequest(BaseModel):
    """一次真机只读对照的输入，期望目标仅用于独立评分。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # 验证时手选的已保存源码组及 Android 序列号。
    group_id: UUID
    device_serial: str = Field(min_length=1, max_length=256, pattern=r"^\S+$")
    # query 同时交给 A/B；expected_target 是此帧中独立确认过的完整 ID 或文案。
    query: str = Field(min_length=1, max_length=256)
    expected_target: str = Field(min_length=1, max_length=256)
    # 两组都需要视觉时，重复对照允许交换先后。
    order: Literal["ab", "ba"] = "ab"
    # 只有显式布尔值 true 才可将所选设备的当前截图用于模型兜底。
    confirm_model_call: StrictBool = False


@contextmanager
def _binding_request(request: Request, *, write: bool = False) -> Iterator[None]:
    """只接受来自本机同源控制台的 request，写操作额外要求绑定意图。

    write 默认为只读。将绑定配置的业务错误转换成对应 HTTP 状态码。
    """
    require_local(request)
    # Codex 登录专用意图与 App 工程绑定独立，沿用其他配置接口的检查方式。
    if write and request.headers.get("x-artemis-app-project-intent") != "configure":
        raise HTTPException(403, "保存 App 工程绑定需要控制台明确确认。")
    try:
        yield
    except service.BindingError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.get("")
def list_groups(request: Request) -> service.SourceGroups:
    """返回本机绑定供控制台选择，未配置时 groups 为空。"""
    with _binding_request(request):
        return service.list_groups()


@router.get("/stats")
def get_source_locating_stats(request: Request) -> dict[str, int | float | None]:
    """读取本机累计源码定位统计，无数据时返回零计数和空耗时。

    不读取源码组配置或视觉缓存页面列表。仅使用已有 SQLite 只读快照，
    读取失败时返回 503，视觉缓存停用时仍可查看已记录的源码统计。
    """
    with _binding_request(request):
        try:
            # 与视觉缓存仪表盘使用同一指标数据库，打开旧库时不补表。
            path = configured_cache().path
            if not path.exists():
                return read_source_locating()
            uri = path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as db:
                db.execute("BEGIN")
                return read_source_locating(db)
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(503, "暂时无法读取源码定位统计。") from exc


@router.post("", status_code=201)
def create_group(request: Request, body: service.SourceGroupInput) -> service.SourceGroup:
    """校验并保存 body 指定的一组目录，返回新组及其 UUID。"""
    with _binding_request(request, write=True):
        return service.create_group(body)


@router.put("/{group_id}")
def update_group(
    request: Request, group_id: UUID, body: service.SourceGroupInput
) -> service.SourceGroup:
    """用 body 的完整配置替换 group_id，返回保存后的组，未知 ID 返回 404。"""
    with _binding_request(request, write=True):
        return service.update_group(str(group_id), body)


@router.delete("/{group_id}")
def delete_group(request: Request, group_id: UUID) -> dict[str, bool]:
    """移除 group_id 对应的绑定，成功返回 deleted，未知 ID 返回 404。"""
    with _binding_request(request, write=True):
        service.delete_group(str(group_id))
    return {"deleted": True}


@router.post("/verify")
async def verify(request: Request, body: CompareRequest) -> dict:
    """返回同帧 A/B 的定位来源、评分、模型调用及毫秒数，不操作目标 App。"""
    with _binding_request(request):
        if request.headers.get("x-artemis-app-project-intent") != "verify":
            raise HTTPException(403, "真机对照需要控制台明确确认。")
        group = await asyncio.to_thread(service.get_group, str(body.group_id))
    return await compare(
        group,
        body.device_serial,
        body.query,
        body.expected_target,
        order=body.order,
        allow_vision=body.confirm_model_call,
    )
