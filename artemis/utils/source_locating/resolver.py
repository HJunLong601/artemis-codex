"""按选中的绑定目录查候选，把相同的匹配规则提供给普通任务和控制台对照。

```text
源码组、当前帧、批量原始查询
  ↓
确认目录仍在绑定范围内，且当前 App 与源码组一致
  └─ 不一致 → 整批返回空结果
  ↓
解析名称、角色和位置，将原句与提取出的名称一并交给各读取器
  ↓
逐条查询优先核对原句的候选；原句无线索时，再核对提取出的名称
  ├─ 当前帧确认唯一目标 → 用原始查询作为键返回坐标
  └─ 无线索或画面无法确认 → 该目标交回原定位流程
```

各读取器只产出源码证据。同一工程可有多种技术栈，候选统一交给 screen.py 消歧。
"""

from pathlib import Path

from artemis.config.app_projects import SourceGroup
from artemis.utils.visual_location_cache import LocationScope

from artemis.utils.source_locating.android.views import read_android_many
from artemis.utils.source_locating.compose.reader import read_compose_many
from artemis.utils.source_locating.flutter.reader import read_flutter, read_flutter_many
from artemis.utils.source_locating.common.query import parse_query
from artemis.utils.source_locating.react_native.reader import read_react_native_many
from artemis.utils.source_locating.common.screen import match_screen
from artemis.utils.source_locating.common.types import SourceHint


def _approved_roots(project_dir: str, additional_dirs: list[str]) -> tuple[Path, ...]:
    """重新核验绑定的绝对路径；目录已移动或改成符号链接时整组失效。"""
    roots = tuple(Path(path) for path in (project_dir, *additional_dirs))
    try:
        if all(
            root.is_absolute() and root.resolve(strict=True) == root and root.is_dir()
            for root in roots
        ):
            return roots
    except (OSError, RuntimeError, ValueError):
        pass
    return ()


def project_labels(project_dir: str, additional_dirs: list[str], query: str) -> tuple[str, ...]:
    """兼容原 Flutter 精确文案查询；仅在同一翻译集合有明确依据时返回候选标签。"""
    roots = _approved_roots(project_dir, additional_dirs)
    if not roots:
        return ()
    groups = {h.labels for h in read_flutter(roots, query) if h.labels}
    return next(iter(groups)) if len(groups) == 1 else ()


def match_queries(group: SourceGroup, scope: LocationScope, queries: list[str]) -> dict[str, dict]:
    """在同一已验证前台帧处理未定位目标，并按原始查询返回可用坐标。

    先查完整名称，再查解析出的控件名。各技术栈的证据在设备上一起消歧；
    完整名称已有来源时，核验失败就交回原流程，避免降级到更宽松的候选。
    本函数同步读本机工程，调用方在工作线程执行。
    """
    roots = _approved_roots(group.project_dir, group.additional_dirs)
    if not roots or scope.application != group.package:
        return {}
    # 先汇集整帧所需的原文和名称，避免一帧的多个控件反复遍历相同工程。
    parsed = {raw: parse_query(raw) for raw in queries if raw.strip()}
    names = tuple(
        dict.fromkeys(
            name for raw, target in parsed.items() for name in (raw.strip(), target.name) if name
        )
    )
    # 混合工程可以同时使用多个框架。各读取器以本机工程元数据启用，结果合并后只核验一次。
    by_name: dict[str, list[SourceHint]] = {name: [] for name in names}
    for reader in (read_flutter_many, read_android_many, read_compose_many, read_react_native_many):
        for name, hints in reader(roots, names).items():
            by_name[name].extend(hints)
    matched = {}
    for raw, target in parsed.items():
        # 精确原句已有线索但未通过真机检查时，停止尝试更宽的名称，避免改点另一个控件。
        for name in dict.fromkeys((raw.strip(), target.name)):
            hints = by_name.get(name, ())
            if hints:
                candidate = match_screen(scope, hints, target)
                if candidate is not None:
                    matched[raw] = candidate
                break
    return matched
