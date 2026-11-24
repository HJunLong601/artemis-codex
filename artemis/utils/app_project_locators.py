"""普通 Android 任务的源码定位入口，管理真机观察、前台复核与后台计数。

```text
普通定位尚未找到的目标
  ↓
选中绑定组，核对最新原图、原生 UI 树和前台包名
  ├─ 无对应绑定或当前画面无效 → 返回空结果
  └─ 条件满足 → 在后台线程读取源码并核对当前帧
                 ↓
               有候选时再确认前台包名、Activity 和截图是否一致
                 ├─ 一致 → 返回本帧坐标
                 └─ 变化或无线索 → 交回原定位流程
```

实际进入的查询批次会异步记录命中数与耗时。本模块只返回定位结果，设备操作由上层执行器负责。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

from artemis.agents.explorer.geometry import resolve_screen_size
from artemis.utils.source_locating.resolver import match_queries, project_labels
from artemis.utils.source_locating.common.screen import (
    Bounds,
    point_inside,
    project_candidate,
    target_bounds,
)
from artemis.utils.visual_location_cache import LocationScope, configured_cache, screen_scope
from artemis.utils.visual_page_fingerprint import status_bar_from_hierarchy

if TYPE_CHECKING:
    from artemis.context import ArtemisContext
    from artemis.graph.state import State

logger = logging.getLogger(__name__)

# 保留既有对照工具及测试使用的导入入口。
__all__ = [
    "Bounds",
    "point_inside",
    "project_candidate",
    "project_labels",
    "resolve_project_locations",
    "target_bounds",
]


async def _record_source_metrics(
    ctx: ArtemisContext, scope: LocationScope, attempts: int, hits: int, elapsed_ms: float
) -> None:
    """在任务后台线程持久化已结束的核对批次。磁盘或配置故障由遥测自行消化。"""
    try:
        await asyncio.to_thread(
            configured_cache(ctx).record_source_locating, scope, attempts, hits, elapsed_ms
        )
    except (OSError, RuntimeError, ValueError):
        logger.debug("Source locating telemetry unavailable")


async def resolve_project_locations(
    ctx: ArtemisContext,
    state: State | None,
    screenshot_path: str,
    queries: list[str],
    cache_scope: LocationScope | None = None,
) -> dict[str, dict]:
    """对该任务的未命中 query 尝试源码定位。只认最新原图及已确认的前台 App。

    手选时只用所选包名的组，自动时在任务快照里按当前包名找唯一组。
    cache_scope 可复用当前定位已读取的屏幕证据。视觉缓存关闭时独立构造只读证据，
    不打开 SQLite。返回原始描述到候选的映射，空结果让调用方继续原定位链。
    核对完成后单独记录范围已确认的批次和耗时，源码命中不新增缓存条目。
    """
    # 前台包名只从实际设备观测取得。这里先确定本次定位可以使用哪些包名和源码组。
    selected = getattr(ctx, "app_source_group", None)
    groups = (
        {selected.package: selected}
        if selected is not None
        else getattr(ctx, "automatic_source_groups", None) or {}
    )
    if (
        not groups
        or not queries
        or str(screenshot_path) != getattr(state, "latest_screenshot", None)
    ):
        return {}
    # 只取原生层级。融合的 OCR、裁剪图或旧图不能贡献可点击证据。
    hierarchy = getattr(state, "latest_ui_hierarchy", None)
    if not isinstance(hierarchy, list) or not hierarchy:
        return {}
    if cache_scope is not None and cache_scope.application not in groups:
        return {}
    # 没有缓存提供前台时，先排除树中不存在任何已绑定 App 的场景，省掉额外设备查询。
    if cache_scope is None and not any(
        isinstance(node, dict) and node.get("package") in groups for node in hierarchy
    ):
        return {}

    from artemis.drivers.factory import get_driver

    # 仅统计真正进入源码查询的批次，计算耗时包含本批准备及前台复查。
    started = perf_counter()
    attempts = 0
    resolved: dict[str, dict] = {}
    scope = cache_scope
    try:
        driver = get_driver(ctx)
        if scope is None:
            package, activity = await asyncio.wait_for(driver.get_current_app(), timeout=2)
            if not package or package not in groups or not activity:
                return {}
            size = resolve_screen_size(ctx, state)
            # 文件读取、图像处理和文案索引都在后台线程，不阻塞任务事件循环。
            scope = await asyncio.to_thread(
                lambda: screen_scope(
                    ctx.device,
                    package,
                    Path(screenshot_path).read_bytes(),
                    hierarchy,
                    size,
                    status_bar_from_hierarchy(hierarchy, size),
                    activity=activity,
                )
            )
        attempts = len(dict.fromkeys(queries))
        results = await asyncio.to_thread(match_queries, groups[scope.application], scope, queries)
        if results and str(screenshot_path) == getattr(state, "latest_screenshot", None):
            # 前台查询本身有等待，返回后还要核对任务仍在使用同一张图。
            foreground = await asyncio.wait_for(driver.get_current_app(), timeout=2)
            if foreground == (scope.application, scope.activity) and str(
                screenshot_path
            ) == getattr(state, "latest_screenshot", None):
                resolved = results
    except Exception as exc:
        # 本机目录、设备或画面暂不可读时，继续正常感知，不把源码路径带入日志。
        logger.debug("Source group locating unavailable (%s)", type(exc).__name__)
    if attempts and scope is not None:
        # 跟随任务的后台工作生命周期，不在已校验坐标返回前等待 SQLite。
        elapsed_ms = (perf_counter() - started) * 1000
        pending = ctx.background_tasks
        telemetry = asyncio.create_task(
            _record_source_metrics(ctx, scope, attempts, len(resolved), elapsed_ms),
            name="source-locating-metrics",
        )
        pending.append(telemetry)
        telemetry.add_done_callback(
            lambda finished: pending.remove(finished) if finished in pending else None
        )
    if resolved:
        logger.info("Source group locating matched %d current UI targets", len(resolved))
    return resolved
