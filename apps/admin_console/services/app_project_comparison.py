"""在同一张真机画面上比较 Flash 定位和源码辅助定位。"""

import asyncio
from contextlib import suppress
from time import perf_counter
from typing import Literal

from fastapi import HTTPException
from langchain_core.language_models.chat_models import BaseChatModel

from artemis.agents.explorer.geometry import pixel_to_norm
from artemis.agents.explorer.perception_tools import load_detector_templates
from artemis.agents.explorer.screen_index import ScreenIndex
from artemis.agents.object_detector.object_detector import _detect_single_label
from artemis.clients.screen_client_factory import create_screen_client
from artemis.config import parse_llm_config
from artemis.config.app_projects import SourceGroup
from artemis.context import ArtemisContext, DeviceContext, DevicePlatform
from artemis.drivers.base import ScreenData
from artemis.drivers.factory import get_driver
from artemis.llm.router import ModelFactory
from artemis.runtime import DeviceExecutionLock, DeviceRegistryError, device_registry
from artemis.runtime.adb_endpoint import current_adb_endpoint
from artemis.runtime.device_lock import DeviceBusyError
from artemis.services.llm import _resolve_endpoint
from artemis.utils.source_locating.resolver import match_queries
from artemis.utils.source_locating.common.screen import Bounds, point_inside, target_bounds
from artemis.utils.visual_location_cache import screen_scope
from artemis.utils.visual_page_fingerprint import status_bar_from_hierarchy

# 预测点采用 0..1000 的 x、y 顺序。
LocateSource = Literal["hierarchy", "project", "visual", "unresolved"]


class _MeasuredModel:
    """只统计本次定位实际派发给模型客户端的请求，包含失败和超时。"""

    def __init__(self, model: BaseChatModel) -> None:
        """用双方共有的模型客户端初始化单组计数，计数从零开始。"""
        self.model = model
        self.calls = 0
        self.received = False

    async def ainvoke(self, messages):
        """派发 detector 构造的单次消息，返回原模型响应或向 detector 抛出异常。"""
        self.calls += 1
        response = await self.model.ainvoke(messages)
        self.received = True
        return response


def _baseline_point(frame: ScreenData, query: str) -> tuple[int, int] | None:
    """复用 Flash 原有的实时标签预检，歧义和无匹配交给后续视觉定位。"""
    candidates = ScreenIndex.from_hierarchy(
        frame.ui_elements, frame.width, frame.height
    ).exact_matches(query)
    if len(candidates) != 1:
        return None
    return pixel_to_norm(*candidates[0].center, frame.width, frame.height)


def _project_point(
    ctx: ArtemisContext, frame: ScreenData, group: SourceGroup, activity: str, query: str
) -> tuple[int, int] | None:
    """只在源码候选被当前树和现有遮挡检查共同证实时返回位置。"""
    # 同普通任务共享原生节点验证；对照仅在这一帧上计分且不写缓存。
    try:
        # 截图像素尺寸同时用于校验控件和系统状态栏，保持普通定位与 A/B 的判定一致。
        size = (frame.width, frame.height)
        scope = screen_scope(
            ctx.device,
            group.package,
            frame.screenshot_bytes,
            frame.ui_elements,
            size,
            status_bar_from_hierarchy(frame.ui_elements, size),
            activity=activity,
        )
    except (OSError, ValueError):
        return None
    candidate = match_queries(group, scope, [query]).get(query)
    if candidate is None:
        return None
    x, y = candidate["coords"]
    return x, y


def _screen_model(ctx: ArtemisContext) -> BaseChatModel:
    """按现有视觉定位路由选主模型。短时对照直接调用该客户端，避免触发全局任务暂停。"""
    try:
        return ModelFactory.get_model(_resolve_endpoint(ctx, "object_detector"))
    except Exception:
        return ModelFactory.get_model(_resolve_endpoint(ctx, "operator"))


async def _locate_visually(
    model: BaseChatModel, ctx: ArtemisContext, frame: ScreenData, query: str
) -> tuple[tuple[int, int] | None, int]:
    """对冻结的屏幕图调用现有 detector，返回单个有效坐标和客户端派发次数。"""
    # 和 Flash 共享模板及整轮超时，输入模型的只有原始描述与当前画面。
    templates, timeout = load_detector_templates()
    measured = _MeasuredModel(model)
    # 新版配置中的超时通常在节点级定义，沿用 detector 在未设置全局超时时的默认行为。
    configured_timeout = getattr(ctx.llm_config, "timeout", None)
    try:
        detections = await asyncio.wait_for(
            _detect_single_label(
                measured,
                ctx,
                frame.screenshot_bytes,
                "image/png" if frame.screenshot_bytes.startswith(b"\x89PNG") else "image/jpeg",
                query,
                templates,
                timeout_val=configured_timeout
                if isinstance(configured_timeout, (int, float))
                else None,
            ),
            timeout=timeout,
        )
    except TimeoutError:
        detections = []
    if measured.calls and not measured.received:
        raise HTTPException(502, "视觉模型没有返回响应，本次对照无法计分。")
    # Detector 的原始输出使用 [y,x]，只接受单个有效点以便独立评分。
    point = detections[0].get("point") if len(detections) == 1 else None
    if (
        isinstance(point, list)
        and len(point) == 2
        and all(type(n) in (int, float) and 0 <= n <= 1000 for n in point)
    ):
        return (int(point[1]), int(point[0])), measured.calls
    return None, measured.calls


def _report(
    frame: ScreenData,
    expected: Bounds,
    point: tuple[int, int] | None,
    source: LocateSource,
    calls: int,
    started: float,
) -> dict:
    """把预测点与独立登记的真机目标框比较。时间是本定位组的毫秒数。"""
    correct = point is not None and point_inside((frame.width, frame.height), expected, point)
    return {
        "coords": point,
        "source": source,
        "correct": correct,
        "vision_calls": calls,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
    }


async def compare(
    group: SourceGroup,
    serial: str,
    query: str,
    expected_target: str,
    *,
    order: Literal["ab", "ba"] = "ab",
    allow_vision: bool = False,
) -> dict:
    """对已选 group、Android serial 的单目标做同帧对照。

    query 是两组共同的描述，expected_target 仅作独立计分。order 指定先后顺序。
    默认仅本地定位；allow_vision 为真时两组用同一模型兜底。
    返回评分、视觉尝试数和毫秒，失败或取消时释放设备锁。
    """
    try:
        device = await device_registry.select_device_async(DevicePlatform.ANDROID, serial)
    except DeviceRegistryError:
        raise HTTPException(409, "请选择一台已连接并授权的 Android 设备。") from None

    # 与普通 Android 任务使用相同的设备和 ADB endpoint 标识。
    lock = DeviceExecutionLock.for_device(
        device,
        "App project comparison",
        ingress="app-projects",
        lock_scope=current_adb_endpoint().identity,
    )
    try:
        lock.acquire(blocking=False)
    except DeviceBusyError:
        raise HTTPException(409, "设备正在执行其他任务。") from None
    driver = None
    try:
        ctx = ArtemisContext(
            device=DeviceContext(
                mobile_platform=device.platform,
                device_id=device.device_id,
                device_name=device.name,
                device_kind=device.kind.value,
            ),
            ui_adb_client=create_screen_client(device.device_id),
        )
        driver = get_driver(ctx)
        started = perf_counter()
        await driver.connect()
        before = await driver.get_current_app()
        if before[0] != group.package or not before[1]:
            raise HTTPException(409, "请让选定源码组的 App 停留在要验证的页面。")
        frame = await driver.get_screen_data()
        if await driver.get_current_app() != before or not frame.ui_elements:
            raise HTTPException(409, "前台页面已切换或控件树不可用，请在稳定页面上重试。")
        observation_ms = round((perf_counter() - started) * 1000, 2)

        # 期望元素只参与计分，避免给实验组或基线泄漏正确答案。
        expected = target_bounds(
            frame.ui_elements,
            (frame.width, frame.height),
            group.package,
            (expected_target,),
            expected_target,
        )
        if expected is None:
            raise HTTPException(422, "期望目标需要在当前树中精确对应一个可见且可点击的控件。")

        # 在第一次需要模型时固定主端点，另一组复用；无视觉调用时无需初始化模型。
        model = None
        outcomes = {}
        for arm in order:
            started = perf_counter()
            point = _baseline_point(frame, query)
            source: LocateSource = "hierarchy"
            calls = 0
            if point is None and arm == "b":
                point = await asyncio.to_thread(_project_point, ctx, frame, group, before[1], query)
                source = "project"
            if point is None and allow_vision:
                if model is None:
                    ctx.llm_config = parse_llm_config()
                    model = _screen_model(ctx)
                point, calls = await _locate_visually(model, ctx, frame, query)
                source = "visual"
            if point is None:
                source = "unresolved"
            outcomes[arm] = _report(frame, expected, point, source, calls, started)
        if await driver.get_current_app() != before:
            raise HTTPException(409, "对照期间前台页面发生变化，请重试。")
        return {
            **outcomes,
            "observation_ms": observation_ms,
            "order": order,
            "vision_enabled": allow_vision,
        }
    finally:
        try:
            if driver is not None:
                with suppress(Exception):
                    await asyncio.wait_for(driver.disconnect(), timeout=5)
        finally:
            lock.release()
