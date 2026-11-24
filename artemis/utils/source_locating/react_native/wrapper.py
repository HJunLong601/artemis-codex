"""核验工程内以原生 Pressable 为根的透明包装器，保留对 testID 和标签属主的明确证据。

```text
调用处导入自定义控件
  → 通过相对路径或本模块 TSConfig 的单目标别名找到源文件
  → 按 Android / native / 通用顺序核对变体，同一级只能有一个文件
  → 定义必须是具名导出的函数，直接返回导入的原生 Pressable
  → JSX 只展开同一个参数，允许额外原样传入拆出的 children 和 style
  ├─ 证明会透传 testID、文案和动作 → 调用处属性可用于设备核验
  └─ 覆盖属性、导出不符或解析不了 → 忽略该包装器候选
```

组件调用点与包装器定义各自按文件签名解析。单独修改包装器即可让下一次匹配重新核验，
即使组件调用文件没有改动。
"""

from functools import lru_cache
import json
from pathlib import Path
import re

from tree_sitter import Node, Parser

from artemis.utils.source_locating.react_native.jsx import (
    JSX_TYPES,
    TSX,
    bindings,
    element_children,
    element_kind,
    element_open_tag,
    expression,
    node_text,
)

# 只比较会被 Metro 在 Android 上选中的候选扩展，并拒绝同优先级的歧义。
_EXTENSIONS = (".tsx", ".ts", ".jsx", ".js")
# 包装模块和 TSConfig 采用相同的单文件读取预算，单位为字节。
_MAX_BYTES = 2 * 1024 * 1024
# JSONC 字符串和注释分别匹配，避免去掉路径字符串里的注释符。
_JSONC = re.compile(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*')
# TypeScript 配置允许尾逗号。删除只紧跟结束符的逗号，保留配置字符串中的同形符号。
_TRAILING_COMMA = re.compile(r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])')


def _safe_file(path: Path, module: Path) -> tuple[Path, int, int, int] | None:
    """只返回 module 下真实文件的物理路径及 mtime/ctime 纳秒、字节数；无文件返回 None。"""
    if not path.is_file() or path.is_symlink():
        return None
    # 检查物理路径，防止通过文件或父目录软链接越过本模块。
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(module) or not resolved.is_relative_to(path.parent.resolve()):
        return None
    info = resolved.stat()
    return (
        (resolved, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
        if info.st_size <= _MAX_BYTES
        else None
    )


@lru_cache(maxsize=16)
def _aliases(path: Path, modified: int, changed: int, size: int) -> tuple[str, dict]:
    """仅解析当前模块的 TSConfig 自身路径映射。文件签名失效后重新读取，继承配置不执行。"""
    with path.open("rb") as source:
        payload = source.read(_MAX_BYTES + 1)
    if len(payload) != size:
        return ".", {}
    try:
        without_comments = _JSONC.sub(
            lambda token: token.group() if token.group().startswith('"') else "",
            payload.decode("utf-8-sig"),
        )
        contents = json.loads(
            _TRAILING_COMMA.sub(
                lambda token: token.group() if token.group().startswith('"') else "",
                without_comments,
            )
        )
        # base 是相对模块的基准目录。paths 保留原始别名配置，后续只接受单目标映射。
        options = contents.get("compilerOptions", {})
        base = options.get("baseUrl", ".")
        paths = options.get("paths", {})
        if isinstance(base, str) and not Path(base).is_absolute() and isinstance(paths, dict):
            return base, paths
    except (AttributeError, UnicodeError, ValueError):
        pass
    return ".", {}


def _reference(caller: Path, module: Path, library: str) -> Path | None:
    """核对 caller 的相对引用或同模块的单目标 TSConfig 别名，不解析外部 npm 包。"""
    if library.startswith(("./", "../")):
        return caller.parent / library
    config = _safe_file(module / "tsconfig.json", module)
    if config is None:
        return None
    base, paths = _aliases(*config)
    # 权重用于选择最具体的映射。候选路径稍后仍需经过文件与模块边界检查。
    matches: list[tuple[int, Path]] = []
    for alias, destinations in paths.items():
        if not isinstance(destinations, list) or len(destinations) != 1:
            continue
        destination = destinations[0]
        if not isinstance(destination, str) or Path(destination).is_absolute():
            continue
        if alias == library and "*" not in destination:
            matches.append((len(alias) + 1, module / base / destination))
        elif alias.endswith("/*") and alias.count("*") == 1 and destination.count("*") == 1:
            prefix = alias[:-1]
            if library.startswith(prefix):
                matches.append(
                    (len(prefix), module / base / destination.replace("*", library[len(prefix) :]))
                )
    if not matches:
        return None
    # TypeScript 优先精确项，其次使用最长静态前缀；同级冲突时无法确认实际引用。
    best = max(rank for rank, _ in matches)
    selected = {path for rank, path in matches if rank == best}
    return next(iter(selected)) if len(selected) == 1 else None


def _parameter(function: Node) -> tuple[str, set[str]]:
    """从一个形参取得 rest 属性变量及是否拆出 children/style；其他解构会改变转发契约。"""
    arguments = function.child_by_field_name("parameters")
    if arguments is None or len(arguments.named_children) != 1:
        return "", set()
    parameter = arguments.named_children[0]
    pattern = parameter.child_by_field_name("pattern") or parameter
    if pattern.type == "identifier":
        return node_text(pattern), set()
    if pattern.type != "object_pattern":
        return "", set()
    # rest 是实际展开的其余 props。forwarded 仅记录从原参数另行取出的 children 或 style。
    rest = None
    forwarded = set()
    for item in pattern.named_children:
        if item.type == "rest_pattern" and len(item.named_children) == 1:
            rest = item.named_children[0]
        elif item.type == "shorthand_property_identifier_pattern" and node_text(item) in {
            "children",
            "style",
        }:
            forwarded.add(node_text(item))
        else:
            return "", set()
    return node_text(rest), forwarded


def _forwarded_pressable(function: Node, imports: dict[str, str]) -> bool:
    """验证唯一 return 直接创建已导入的 Pressable，全部定位相关 props 由同一参数透传。"""
    parameter, extracted = _parameter(function)
    body = function.child_by_field_name("body")
    if not parameter or body is None:
        return False
    # 允许 return 外有注释。若有其他语句，参数是否在返回前改写无法由此入口证明。
    statements = [node for node in body.named_children if node.type != "comment"]
    if len(statements) != 1 or statements[0].type != "return_statement":
        return False
    values = [node for node in statements[0].named_children if node.type != "comment"]
    actual = expression(values[0]) if len(values) == 1 else None
    if (
        actual is None
        or actual.type not in JSX_TYPES
        or (tag := element_open_tag(actual)) is None
        or element_kind(tag, imports) != "Pressable"
    ):
        return False
    # 允许保留唯一 rest 展开以及原值 style，禁止新写标签、ID、press 和无障碍属性。
    seen = set()
    for attr in tag.children_by_field_name("attribute"):
        value = expression(attr)
        if value is not None and value.type == "spread_element":
            if "props" in seen or node_text(value.named_children[0]) != parameter:
                return False
            seen.add("props")
            continue
        parts = attr.named_children
        if (
            len(parts) != 2
            or node_text(parts[0]) != "style"
            or "style" not in extracted
            or "style" in seen
            or node_text(expression(parts[1])) != "style"
        ):
            return False
        seen.add("style")
    children = element_children(actual)
    return "props" in seen and (
        len(children) == 1 and node_text(expression(children[0])) == "children"
        if "children" in extracted
        else not children
    )


@lru_cache(maxsize=128)
def _exports(path: Path, modified: int, changed: int, size: int) -> frozenset[str]:
    """按包装源码签名缓存已验证的命名导出，修改包装器会立刻撤销调用点的既有证据。"""
    with path.open("rb") as source:
        payload = source.read(_MAX_BYTES + 1)
    if len(payload) != size or b"react-native" not in payload or b"Pressable" not in payload:
        return frozenset()
    document = Parser(TSX).parse(payload).root_node
    if document.has_error:
        return frozenset()
    imports, _, _ = bindings(document)
    names = set()
    for statement in document.named_children:
        if statement.type != "export_statement" or any(
            part.type == "default" for part in statement.children
        ):
            continue
        function = statement.child_by_field_name("declaration")
        if (
            function is not None
            and function.type == "function_declaration"
            and _forwarded_pressable(function, imports)
        ):
            names.add(node_text(function.child_by_field_name("name")))
    return frozenset(names)


def forwards_pressable(caller: Path, module: Path, library: str, name: str) -> bool:
    """核对 caller 从 library 导入的 name 是否透明传给同模块的原生 Pressable。

    module 是已确认的 React Native 模块根。同步只读源码及配置，调用方在后台批次执行。
    文件不可读、目标不唯一、语法或透传形式不被支持时返回 False。
    """
    try:
        target = _reference(caller, module, library)
        if target is None:
            return False
        # Android 变体先于 native 和通用实现；显式引用扩展时不推断其他文件。
        options = (
            [(target,)]
            if target.suffix in _EXTENSIONS
            else [
                tuple(
                    Path(f"{stem}{variant}{suffix}")
                    for stem in (target, target / "index")
                    for suffix in _EXTENSIONS
                )
                for variant in (".android", ".native", "")
            ]
        )
        for candidates in options:
            # 命中首个存在的变体后，只验证这一层。高优先级文件不能用低优先级证据代替。
            stamps = {
                stamp for path in candidates if (stamp := _safe_file(path, module)) is not None
            }
            if stamps:
                return len(stamps) == 1 and name in _exports(*next(iter(stamps)))
    except (OSError, RuntimeError, UnicodeError, ValueError, TypeError):
        return False
    return False
