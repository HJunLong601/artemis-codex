"""从绑定的 React Native 工程提取可由 Android 无障碍树核验的 JSX 线索。

```text
用户绑定的目录 + 本轮要找的控件
  → 看 package.json，确认 React Native 模块及本地工作区
  → 遍历该模块的 Android 与通用源码，按文件签名复用语法解析结果
  → 原生组件直接取证据，包装器先核对参数是否传给原生 Pressable
  → 原始 testID 精确命中时返回 ID 线索，否则按规范化文案找候选
  → 交给调用方用当前 UI 树确认唯一可操作的控件
  └─ 扫描出错或超出整批预算 → 整批交回原定位流程
```

源码只提供控件名称、原始 ID 和声明出处，点击坐标来自设备当前屏幕。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from functools import lru_cache
import json
import os
from pathlib import Path
from stat import S_ISREG

from tree_sitter import Parser

from artemis.agents.explorer.screen_index import normalize_text
from artemis.utils.source_locating.react_native.jsx import (
    JSX_TYPES,
    TSX,
    bindings,
    element_kind,
    element_open_tag,
    element_reachable,
    hint_for_element,
    node_text,
    walk_nodes,
)
from artemis.utils.source_locating.react_native.wrapper import forwards_pressable
from artemis.utils.source_locating.common.types import SourceHint

# 只分析 Android 与通用源文件，跳过测试、依赖和编译产物。
_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
# 各技术栈常见的依赖、生成物与测试目录。这里按目录全名判断，不依赖项目根目录命名。
_EXCLUDED = {
    ".git",
    "node_modules",
    "build",
    "dist",
    "coverage",
    "vendor",
    "__tests__",
    "__mocks__",
    "__fixtures__",
    "__snapshots__",
    "tests",
    "test",
    "e2e",
    "storybook",
}
# 每组的目录、源文件和累计读取预算，大小以字节计；超限时整批返回无证据。
_MAX_DIRECTORIES = 8192
_MAX_FILES = 8192
_MAX_BYTES = 128 * 1024 * 1024
_MAX_FILE_BYTES = 2 * 1024 * 1024


# None 表示声明已经核验为原生组件；(路径，导出名) 需要读取该包装器的当前源码。
_Use = tuple[SourceHint, tuple[str, str] | None]


@lru_cache(maxsize=4096)
def _read_source(path: Path, edited: int, changed: int, size: int) -> tuple[_Use, ...]:
    """按路径、mtime/ctime 纳秒及字节数复用有限的解析结果，在读取发生变化时让整轮重新回退。"""
    # 最多额外读取 1 字节，以便发现文件在 stat 之后变大。
    with path.open("rb") as stream:
        source = stream.read(_MAX_FILE_BYTES + 1)
    if len(source) != size:
        raise ValueError("React Native source changed during lookup")
    # 只分析可能从原生组件或本地包装器读到定位属性的文件。
    if b"import" not in source or not any(
        marker in source
        for marker in (b"testID", b"accessibilityLabel", b"aria-label", b"onPress", b"placeholder")
    ):
        return ()
    # 错误恢复的语法树可能漏掉遮蔽声明，整份文件有语法错误时不采纳其中候选。
    root = Parser(TSX).parse(source).root_node
    if root.has_error:
        return ()
    imports, constants, wrappers = bindings(root)
    if not imports and not wrappers:
        return ()
    # 包装器调用点只暂存属性证据，包装器定义可能单独变更，使用前还要独立核验。
    hints = []
    for element in walk_nodes(root):
        if (
            element.type not in JSX_TYPES
            or (tag := element_open_tag(element)) is None
            or not element_reachable(element)
        ):
            continue
        kind = element_kind(tag, imports)
        wrapper = None if kind else wrappers.get(node_text(tag.child_by_field_name("name")))
        if not kind and wrapper is None:
            continue
        origin = f"{path}:{tag.start_point.row + 1}"
        hint = hint_for_element(element, tag, kind or "Pressable", imports, constants, origin)
        if hint is not None:
            hints.append((hint, wrapper))
    return tuple(hints)


def _project_manifest(path: Path) -> tuple[bool, tuple[str, ...]]:
    """从 package.json 返回是否声明 React Native 及工作区路径；不会执行脚本或加载依赖。"""
    if not path.is_file() or path.is_symlink():
        return False, ()
    with path.open("rb") as stream:
        data = stream.read(_MAX_FILE_BYTES + 1)
    if len(data) > _MAX_FILE_BYTES:
        return False, ()
    try:
        manifest = json.loads(data)
    except (ValueError, UnicodeError):
        return False, ()
    if not isinstance(manifest, dict):
        return False, ()
    # 运行依赖、开发依赖和 peerDependencies 都可声明原生模块的 React Native 来源。
    native = any(
        isinstance(manifest.get(field), dict) and "react-native" in manifest[field]
        for field in ("dependencies", "devDependencies", "peerDependencies")
    )
    workspaces = manifest.get("workspaces")
    # npm 通常使用数组，而 Yarn 也支持 workspaces.packages。
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    return native, (
        tuple(member for member in workspaces if isinstance(member, str))
        if isinstance(workspaces, list)
        else ()
    )


def _explicit_workspace(directory: Path, workspaces: tuple[str, ...]) -> Iterator[Path]:
    """返回位于绑定目录内的显式成员；通配成员由有界的普通目录发现处理。"""
    for value in workspaces:
        member = directory / value
        if (
            member != directory
            and member.is_relative_to(directory)
            and not any(
                part.startswith(".") or part.casefold() in _EXCLUDED
                for part in member.relative_to(directory).parts
            )
            and not any(mark in value for mark in "*?[")
            and member.is_dir()
            and member.resolve(strict=True) == member
            and (member / "package.json").is_file()
        ):
            yield member


def _discover(roots: tuple[Path, ...]) -> Iterator[tuple[Path, int, int, int, Path]]:
    """有界枚举绑定根下的 RN 模块与普通源码。文件签名随路径返回，已安装依赖和符号链接被排除。"""
    # project_dirs 记录已进过的目录所属 RN 模块；发现独立 package 后重新核验其依赖。
    project_dirs: dict[Path, Path | None] = {}
    # 对重叠根目录去重。计数以普通源码文件为单位，体积以字节为单位。
    visited: set[Path] = set()
    count = total = 0
    # 先从实际绑定根出发，遇到显式工作区成员才追加它的真实目录。
    pending = list(roots)
    for root in pending:
        if not root.is_absolute() or root.resolve(strict=True) != root or not root.is_dir():
            raise ValueError("Invalid React Native source root")
        # Flutter 根目录的常见配置不包含可运行的 JS 模块；遇到 JS 工作区证据才继续发现。
        if (
            (root / "pubspec.yaml").is_file()
            and not (root / "package.json").is_file()
            and not (root / "pnpm-workspace.yaml").is_file()
        ):
            continue
        for current, directories, files in os.walk(root, followlinks=False):
            directory = Path(current)
            if directory in visited:
                directories.clear()
                continue
            visited.add(directory)
            if len(visited) > _MAX_DIRECTORIES:
                raise ValueError("React Native directory scan exceeds budget")
            # 在原列表上剪枝，阻止 os.walk 进入目录，而非只忽略走到的文件。
            directories[:] = [
                name
                for name in directories
                if not name.startswith(".")
                and name.casefold() not in _EXCLUDED
                and not (name == "Pods" and directory.name == "ios")
                and not (directory / name).is_symlink()
            ]
            # 外层根可能是 monorepo；在实际项目/工作区成员声明之前只枚举目录，不读JS。
            module = project_dirs.get(directory.parent)
            if "package.json" in files:
                native, workspace = _project_manifest(directory / "package.json")
                module = directory if native else None
                # 元数据给出的深层真实模块也应被发现，不能只按默认目录深度猜测。
                for member in _explicit_workspace(directory, workspace):
                    if member not in pending and member not in visited:
                        pending.append(member)
                        if len(pending) > _MAX_DIRECTORIES:
                            raise ValueError("React Native workspace scan exceeds budget")
                if (
                    module is None
                    and not workspace
                    and not (directory / "pnpm-workspace.yaml").is_file()
                ):
                    directories.clear()
            if module is None and len(directory.relative_to(root).parts) >= 4:
                # 没有任何 RN 声明时只核对浅层项目层级，深层独立工程使用显式绑定。
                directories.clear()
            project_dirs[directory] = module
            if module is None:
                continue
            for name in files:
                path = directory / name
                if (
                    path.suffix not in _EXTENSIONS
                    or name.endswith(".d.ts")
                    or any(
                        marker in name
                        for marker in (".test.", ".spec.", ".stories.", ".ios.", ".web.")
                    )
                ):
                    continue
                info = path.stat(follow_symlinks=False)
                if not S_ISREG(info.st_mode):
                    continue
                count += 1
                total += info.st_size
                if count > _MAX_FILES or total > _MAX_BYTES:
                    raise ValueError("React Native source scan exceeds budget")
                if info.st_size <= _MAX_FILE_BYTES:
                    yield path, info.st_mtime_ns, info.st_ctime_ns, info.st_size, module


def read_react_native_many(
    roots: tuple[Path, ...], targets: tuple[str, ...]
) -> dict[str, list[SourceHint]]:
    """在规范化的本机绑定目录 roots 中批查 targets。返回原查询到同节点静态证据的映射。

    名称按统一文本规范化匹配。testID 按原值精确匹配，在 Android 无障碍树中保留该原值。
    同步只读，调用方在工作线程执行；读盘失败或扫描超限返回空结果，不保留部分目录的候选。
    """
    # ID 比较使用原始值而非标签索引，以保留大小写、点号和其他自定义标记。
    result: dict[str, list[SourceHint]] = {target: [] for target in targets}
    if not result:
        return result
    names = {target: normalize_text(target) for target in result if normalize_text(target)}
    # 明确的 ID 查询使用标识自身，不能退回同名文字而选中缺少该 ID 的另一个控件。
    exact_ids: dict[str, list[SourceHint]] = {}
    try:
        for path, modified, changed, size, module in _discover(roots):
            for hint, wrapper in _read_source(path, modified, changed, size):
                labels = {normalize_text(label) for label in hint.labels}
                # 一份源码证据可同时回答多个查询。先筛查询，再为相关项读取包装器。
                matching = [
                    (target, name)
                    for target, name in names.items()
                    if target == hint.resource_id or name in labels
                ]
                if (
                    not matching
                    or wrapper is not None
                    and not forwards_pressable(path, module, *wrapper)
                ):
                    continue
                for target, _ in matching:
                    if target == hint.resource_id:
                        exact_ids.setdefault(target, []).append(replace(hint, labels=()))
                    else:
                        result[target].append(hint)
    except (OSError, RuntimeError, ValueError, UnicodeError):
        return {target: [] for target in result}
    result.update(exact_ids)
    return result
