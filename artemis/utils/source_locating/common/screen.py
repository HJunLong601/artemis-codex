"""在同一帧的原生 UI 树中核对源码证据与几何约束，不执行设备操作。

```text
源码候选、查询约束、当前帧原生 UI 树
  ↓
筛出本 App 内可见、启用、可点击，且符合角色和区域的节点
  ↓
有源码 ID 时优先匹配 ID；双方都有文案时同时核对文案
  └─ 当前帧未暴露候选 ID → 仅无 ID 节点可凭同一声明的文案匹配
  ↓
恰好一个节点？
  ├─ 否 → 返回空结果
  └─ 是 → 检查中心点是否可用，排除遮挡、系统区域和模态干扰
           ├─ 核验通过 → 返回本帧控件框和归一化点击点
           └─ 核验失败 → 返回空结果
```

Android XML 的未限定资源名按「当前包名:id/名称」核对；Flutter、RN、Compose 的语义标识使用设备原值。
"""

from collections import Counter

from artemis.agents.explorer.geometry import norm_to_pixel, pixel_to_norm
from artemis.agents.explorer.screen_index import normalize_text
from artemis.utils.visual_cache_guards import has_local_target
from artemis.utils.visual_location_cache import LocationScope
from artemis.utils.visual_page_fingerprint import node_bounds, target_outside_ignored_regions

from artemis.utils.source_locating.common.query import TargetQuery
from artemis.utils.source_locating.common.types import SourceHint

# 当前设备截图上的像素矩形，依次为左、上、右、下。
Bounds = tuple[int, int, int, int]


def observed_id(node: dict) -> str:
    """读取本帧节点的完整标识，兼容三种驱动字段名。标识值区分大小写。"""
    return node.get("resource-id") or node.get("resource_id") or node.get("resourceId") or ""


def visible_labels(node: dict) -> set[str]:
    """取得控件文字及无障碍描述。多行描述仅整段或首行可作标题，后续行可能是当前值。"""
    return {
        normalize_text(line)
        for text in (node.get("text"), node.get("content-desc") or node.get("content_desc"))
        if isinstance(text, str) and text.strip()
        for line in (text, text.strip().splitlines()[0])
    }


def actionable_bounds(node: dict, package: str, size: tuple[int, int]) -> Bounds | None:
    """核验前台 package、可见性、可操作性及完整屏内像素范围；无法证实时返回 None。"""
    if not isinstance(node, dict) or node.get("package") != package:
        return None
    bounds = node_bounds(node)
    if (
        bounds
        and 0 <= bounds[0] < bounds[2] <= size[0]
        and 0 <= bounds[1] < bounds[3] <= size[1]
        and node.get("enabled") in (True, "true")
        and node.get("clickable") in (True, "true")
        and node.get("visible") not in (False, "false")
        and node.get("visible-to-user") not in (False, "false")
    ):
        return bounds
    return None


def target_bounds(
    hierarchy: list,
    size: tuple[int, int],
    package: str,
    labels: tuple[str, ...],
    resource_id: str = "",
) -> Bounds | None:
    """在 package 的当前 UI 树查唯一可点击的控件。返回全屏像素矩形或 None。"""
    # size 是该图的宽、高。多个可点项同时匹配时保留歧义。
    wanted = {normalize_text(text) for text in labels} - {""}
    # 对照的期望框来自人工指定的文字或完整 ID，不借用源码组提供的映射。
    matches = [
        bounds
        for node in hierarchy
        if (bounds := actionable_bounds(node, package, size))
        and ((resource_id and observed_id(node) == resource_id) or wanted & visible_labels(node))
    ]
    return matches[0] if len(matches) == 1 else None


def point_inside(size: tuple[int, int], bounds: Bounds, point: tuple[int, int]) -> bool:
    """把 0..1000 的归一化点转换为像素，检查是否仍在矩形内。"""
    px, py = norm_to_pixel(*point, *size)
    return bounds[0] <= px < bounds[2] and bounds[1] <= py < bounds[3]


def _same_id(hint: SourceHint, actual: str, package: str) -> bool:
    """按标识来源核对实际 ID；XML 的局部资源名须与当前包的完整设备 ID 对应。"""
    if not hint.android_id or ":id/" in hint.resource_id:
        return actual == hint.resource_id
    namespace, separator, entry = actual.partition(":id/")
    return bool(separator and namespace == package and entry == hint.resource_id)


def match_screen(scope: LocationScope, hints: list[SourceHint], query: TargetQuery) -> dict | None:
    """把同一声明中的 ID、标签与 query 的角色、区域一起在本帧验证。

    不按分数猜测两个节点中的一个。即使两个节点有相同矩形，也属于歧义；最终候选
    还需通过遮挡、忽略区与已有局部目标检查，才返回 0..1000 的归一化坐标。
    """
    # 一条查询可能既有翻译词条，又有实际控件声明。入口优先用已关联的点击回调。
    if query.role == "entry":
        actions = [hint for hint in hints if hint.action == "tap"]
        if actions:
            hints = [hint for hint in hints if hint.action == "tap" or hint.resource_id]
    # 每个原生节点只枚举一次，避免两个指向同一节点的源码声明被误计为两个控件。
    eligible = [
        (bounds, observed_id(node), visible_labels(node))
        for node in scope.hierarchy
        if (bounds := actionable_bounds(node, scope.application, scope.image_size))
        and query.matches_node(node, bounds, scope.image_size)
    ]
    # 带 ID 的同控件声明优先于独立翻译。旧安装包未暴露 ID 时，可在没有 ID 的节点
    # 上使用该声明自身的文字；明确不同的 ID 不能退化为同名文案命中。
    identified = [hint for hint in hints if hint.resource_id]
    relevant = identified or hints
    # 即便目标 ID 对应的节点已禁用或不符合区域，也要保留该证据，避免改点同名控件。
    visible_ids = {
        observed_id(node)
        for node in scope.hierarchy
        if isinstance(node, dict) and node.get("package") == scope.application and observed_id(node)
    }
    id_on_screen = any(
        _same_id(hint, actual, scope.application) for hint in identified for actual in visible_ids
    )
    # 同一个 ID 对应多行时，空文案不能证明它就是源码所指的那一行。
    id_counts = Counter(actual for _, actual, _ in eligible if actual)
    # 不同声明的标签各自保留，避免把不同组件上的标识和文案拼成证据。
    prepared = [(h, {normalize_text(v) for v in h.labels}) for h in relevant]
    matched: Bounds | None = None
    # 仅当采用真机标识匹配时记录实际 ID；纯文案定位继续采用原有模态检查。
    verified_id = ""
    for bounds, actual_id, titles in eligible:
        for hint, names in prepared:
            if hint.resource_id and (actual_id or id_on_screen):
                if not _same_id(hint, actual_id, scope.application):
                    continue
                # 多行共用相同 ID 时，缺少可对照的文案会留下歧义，不能靠遍历顺序选择目标。
                if names and not titles and id_counts[actual_id] > 1:
                    return None
                if titles and names and not titles.intersection(names):
                    continue
            elif not titles.intersection(names):
                continue
            if matched is not None:
                return None
            matched = bounds
            if hint.resource_id and actual_id:
                verified_id = actual_id
            break
    return _verified_point(scope, matched, verified_id) if matched is not None else None


def project_candidate(scope: LocationScope, labels: tuple[str, ...]) -> dict | None:
    """兼容仅持有静态译文的定位调用，仍须经当前 UI 树、唯一性和遮挡核验。"""
    return (
        match_screen(scope, [SourceHint(labels=labels)], TargetQuery(name="")) if labels else None
    )


def _verified_point(scope: LocationScope, bounds: Bounds, verified_id: str) -> dict | None:
    """核对像素框中心并返回归一化位置。verified_id 是已核实的原生 ID，空串表示按文案。"""
    size = scope.image_size
    left, top, right, bottom = bounds
    point = pixel_to_norm((left + right) / 2, (top + bottom) / 2, *size)
    if not point_inside(size, bounds, point):
        return None
    # 真机底栏可能被同包但更宽的可点内容覆盖。不能因为它超出目标框就忽略重叠。
    px, py = norm_to_pixel(*point, *size)
    for node in scope.hierarchy:
        other = node_bounds(node)
        if (
            other
            and other != bounds
            and other[0] <= px < other[2]
            and other[1] <= py < other[3]
            and node.get("clickable") in (True, "true")
            and node.get("visible") not in (False, "false")
            and node.get("visible-to-user") not in (False, "false")
            and (
                node.get("text")
                or node.get("content-desc")
                or node.get("content_desc")
                or observed_id(node)
            )
        ):
            return None
    # 与现有视觉定位同样使用归一化 x、y 和左上右下边界。
    candidate = {
        "coords": list(point),
        "bounds": [*pixel_to_norm(left, top, *size), *pixel_to_norm(right, bottom, *size)],
        "source": "project",
    }
    if target_outside_ignored_regions(candidate, size, scope.ignored_regions) and has_local_target(
        scope, candidate, verified_target_id=verified_id
    ):
        return candidate
    return None
