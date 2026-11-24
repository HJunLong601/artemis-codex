"""从 Compose 的 Kotlin 语法树提取同一控件的标识与静态标签，并交给原生帧核验。

```text
绑定的 Gradle 模块和待查目标
  → 枚举有界的 Kotlin 源集，按文件签名复用已解析的结果
  → 检查 import 和作用域，提取同一控件的静态 testTag、文字或语义描述
  → 遇到 R.string 引用，按已确认的资源命名空间解析各语言文案
  → 按目标整理源码线索
      ├─ 精确 testTag → 只提交原始 ID
      ├─ 显式资源键 → 只接纳确有该 R.string 引用的控件
      └─ 文字描述 → 比对静态文案及资源别名
  → 由当前 Android UI 树确认唯一、可操作的候选
```
"""

from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
from itertools import islice
import os
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from tree_sitter import Language, Node, Parser
import tree_sitter_kotlin

from artemis.agents.explorer.screen_index import normalize_text
from artemis.utils.source_locating.android.resources import (
    _entries,
    _local_r_namespace,
    _modules,
    _real_dir,
    _record_strings,
    _resource_dirs,
    _resolve_string,
)
from artemis.utils.source_locating.common.types import SourceHint

# 单组一次扫描最多检查的 Kotlin 文件数量、路径数量和源文件字节总量。
_MAX_FILES = 8192
_MAX_PATHS = 32768
_MAX_BYTES = 64 * 1024 * 1024
# 单个 Kotlin 文件或 values XML 上限为 512 KiB，越限即放弃本轮 Compose 证据。
_MAX_FILE_BYTES = 512 * 1024
# 只从实际加载的 Android 资源目录取翻译，单批次最多读取 2048 个 values XML。
_MAX_RESOURCES = 2048
# 捕获语法节点的静态 ID、字符串和活动工程布局。无需启动 Gradle 或 Kotlin 编译器。
_LANGUAGE = Language(tree_sitter_kotlin.language())
# 同一 Compose API 的包名契约，用 import 验证，避免拿同名业务函数当成 SDK 控件。
_MATERIAL = ("androidx.compose.material", "androidx.compose.material3")
_BUTTONS = {
    "Button",
    "TextButton",
    "OutlinedButton",
    "FilledTonalButton",
    "ElevatedButton",
    "IconButton",
    "FilledIconButton",
    "FilledTonalIconButton",
    "OutlinedIconButton",
    "FloatingActionButton",
    "SmallFloatingActionButton",
    "LargeFloatingActionButton",
}
_TEXT_FIELDS = {"TextField", "OutlinedTextField"}
_COMMENTS = {"line_comment", "multiline_comment"}
_SCOPE_TYPES = {"source_file", "block", "class_body", "lambda_literal"}
# 仅接受固定 R.string 路径。全限定 R 或显式 import 的别名在读取资源前另行核验。
_RESOURCE = re.compile(
    r"(?:(?P<owner>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.)?string\.(?P<key>[A-Za-z_]\w*)", re.ASCII
)
# 排除测试 APK 源集和生成文件；源组自定义目录可直接绑定相应 Gradle 模块。
_TEST_SOURCE = re.compile(r"(?i)^(?:androidTest|testFixtures|test|benchmark|screenshot)")
# Android 构建自带的标准 Kotlin/Java 源目录，两种目录均可能放 Kotlin 文件。
_SOURCE_DIRS = ("kotlin", "java")


@dataclass(frozen=True)
class _Label:
    """一个可由字面字符串或明确 R 带来的名称，翻译留给当前绑定中的资源命名空间。"""

    # literal 非空表示本控件声明的固定标签，其余字段为空。
    literal: str = ""
    # owner 是被 import 或完整引用确认的 Android 资源命名空间。
    owner: str = ""
    # key 是已确认 R 的 string 条目名称。
    key: str = ""


@dataclass(frozen=True)
class _Evidence:
    """某次 Compose 调用的标签或 testTag 证据，尚未与真机树匹配。"""

    # 同一节点的直接可见文本和无障碍标签，可同时为空（仅 testTag）。
    labels: tuple[_Label, ...]
    # testTag 按 Kotlin 字面字符串保留大小写和标点。
    tag: str
    # 只有同一声明具有明确的点击入口才设为 True。
    clickable: bool


@dataclass
class _Bindings:
    """单个 Kotlin 文件的轻量语法索引，避免把动态值或其他作用域里的同名对象当作固定标签。"""

    # package_name 是文件声明的 Kotlin 包；namespace 是构建文件确认的 Android R 包。
    package_name: str
    namespace: str
    # 符号别名到全限定 import 的映射；冲突的显式导入直接舍弃该文件。
    imports: dict[str, str]
    # 文件中显式启用的星号 import，按框架 API 的准确包名检查。
    wildcards: set[str]
    # 包含同名定义的符号不能绑定到 SDK 函数和唯一只读变量。
    definitions: Counter[str]
    # 静态 val 的初始化表达式及所在作用域，可追踪本文件内的少量局部别名。
    values: dict[str, tuple[Node, Node]]

    def is_api(self, name: str, path: str) -> bool:
        """核验调用符号来自 path 指定的 Compose API，包含可明确确认的 import 别名。"""
        if self.definitions[name]:
            return False
        scope, exported = path.rsplit(".", 1)
        if name in self.imports:
            return self.imports[name] == path
        return name == exported and scope in self.wildcards

    def value(self, node: Node) -> Node:
        """在 node 局部作用域内解引用一次早于调用的唯一静态 val，找不到就返回原语法节点。"""
        if node.type != "identifier":
            return node
        name = _text(node)
        saved = self.values.get(name)
        if not saved or self.definitions[name] != 1:
            return node
        position, scope = saved
        if (
            scope.start_byte <= node.start_byte < scope.end_byte
            and position.end_byte <= node.start_byte
        ):
            return position
        return node

    def resource_owner(self, expression: str) -> tuple[str, str] | None:
        """获取 R.string 引用的命名空间和键；无法证明 R 的来源时不猜测。"""
        match = _RESOURCE.fullmatch(expression)
        if match is None or not (owner := match["owner"]):
            return None
        if "." in owner:
            return (owner[:-2], match["key"]) if owner.endswith(".R") else None
        if self.definitions[owner]:
            return None
        if owner in self.imports:
            imported = self.imports[owner]
            return (imported[:-2], match["key"]) if imported.endswith(".R") else None
        if owner == "R" and self.namespace and self.package_name == self.namespace:
            return self.namespace, match["key"]
        return None


def _text(node: Node) -> str:
    """读取节点对应的 UTF-8 源码片段；文件解码异常由模块入口统一回退。"""
    return (node.text or b"").decode("utf-8")


def _children(node: Node) -> list[Node]:
    """仅返回非注释的语义节点，确保注释不会改变声明范围或给出标签。"""
    return [child for child in node.named_children if child.type not in _COMMENTS]


def _walk(node: Node) -> Iterator[Node]:
    """按语法树迭代所有非注释节点，避免递归深度取决于外部输入。"""
    remaining = [node]
    while remaining:
        current = remaining.pop()
        yield current
        remaining.extend(reversed(_children(current)))


def _literal(node: Node) -> str:
    """只读取完整的 Kotlin 静态字符串，跳过插值、待处理转义及超过 256 字符的值。"""
    if node.type != "string_literal" or any(c.type != "string_content" for c in _children(node)):
        return ""
    value = _text(node)[1:-1]
    return value if value and len(value) <= 256 and "$" not in value and "\\" not in value else ""


def _call(node: Node) -> tuple[Node, dict[str, Node], list[Node], Node | None] | None:
    """拆出 Compose/Kotlin 调用的实际函数、命名参数、位置参数和可选的直接尾部 lambda。"""
    if node.type != "call_expression" or node.has_error:
        return None
    parts = _children(node)
    if not parts:
        return None
    callback = None
    if parts[-1].type == "annotated_lambda":
        wrapper = _children(parts.pop())
        callback = wrapper[0] if len(wrapper) == 1 and wrapper[0].type == "lambda_literal" else None
    if not parts:
        return None
    if len(parts) == 1 and parts[0].type == "call_expression":
        parts = _children(parts[0])
    if not parts or len(parts) > 2 or (len(parts) == 2 and parts[1].type != "value_arguments"):
        return None
    named: dict[str, Node] = {}
    positional: list[Node] = []
    for arg in _children(parts[1]) if len(parts) == 2 else ():
        fields = _children(arg)
        if len(fields) == 1:
            positional.append(fields[0])
        elif len(fields) == 2 and fields[0].type == "identifier" and _text(fields[0]) not in named:
            named[_text(fields[0])] = fields[1]
        else:
            return None
    return parts[0], named, positional, callback


def _label(node: Node, bindings: _Bindings) -> _Label | None:
    """从同一参数解析固定文字或无格式参数的 Compose stringResource。"""
    expression = bindings.value(node)
    if label := _literal(expression):
        return _Label(literal=label)
    parsed = _call(expression)
    if parsed is not None:
        callee, named, positional, callback = parsed
        if (
            callee.type == "identifier"
            and bindings.is_api(_text(callee), "androidx.compose.ui.res.stringResource")
            and not callback
            and len(named) + len(positional) == 1
        ):
            ref = named.get("id") or (positional[0] if positional else None)
            if ref is not None and (
                resource := bindings.resource_owner(_text(bindings.value(ref)))
            ):
                return _Label(owner=resource[0], key=resource[1])
    return None


def _same_widget_label(
    name: str, named: dict[str, Node], positional: list[Node], bindings: _Bindings
) -> _Label | None:
    """按官方控件参数认领自身标签；不从邻近 XML/Kotlin 元素猜测。"""
    material = any(bindings.is_api(name, f"{base}.Text") for base in _MATERIAL)
    annotated = bindings.is_api(name, "androidx.compose.foundation.text.BasicText")
    picture = any(bindings.is_api(name, f"{base}.Icon") for base in _MATERIAL) or bindings.is_api(
        name, "androidx.compose.foundation.Image"
    )
    if not (material or annotated or picture):
        return None
    field = named.get("text" if material or annotated else "contentDescription")
    if field is None and positional:
        position = 0 if material or annotated else 1
        if len(positional) > position:
            field = positional[position]
    return _label(field, bindings) if field is not None else None


def _child_label(callback: Node | None, bindings: _Bindings) -> _Label | None:
    """取官方有语义合并的按钮/输入控件直接包含的唯一 Text 或 Icon 的标签。

    子控件自设 modifier 或有多条语句均可能改变语义树，不把它的标签移动给父控件。
    """
    if callback is None or callback.type != "lambda_literal":
        return None
    statements = _children(callback)
    child = _call(statements[0]) if len(statements) == 1 else None
    if child is None:
        return None
    callee, named, positional, nested = child
    if (
        callee.type != "identifier"
        or nested is not None
        or "modifier" in named
        or len(positional) > 2
    ):
        return None
    # Text 第二个位置参数是 modifier；不能把有独立语义的 Text 放到父按钮上。
    if len(positional) > 1 and not any(
        bindings.is_api(_text(callee), f"{base}.Icon") for base in _MATERIAL
    ):
        return None
    return _same_widget_label(_text(callee), named, positional, bindings)


def _modifier(node: Node | None, bindings: _Bindings) -> tuple[set[str], set[_Label], bool]:
    """沿同一调用的 modifier 接收器链提取原始 testTag、静态 contentDescription 与点击语义。

    仅读取链上直接语义函数。嵌套参数中其他节点的标签不会沿链传播；多 ID 的冲突留给调用方跳过。
    """
    tags: set[str] = set()
    labels: set[_Label] = set()
    clickable = False
    while node is not None:
        parsed = _call(bindings.value(node))
        if parsed is None:
            break
        method, named, positional, callback = parsed
        receiver = _children(method)
        if (
            method.type != "navigation_expression"
            or len(receiver) != 2
            or receiver[-1].type != "identifier"
        ):
            break
        name = _text(receiver[-1])
        if bindings.is_api(name, "androidx.compose.ui.platform.testTag"):
            arg = named.get("tag") or (positional[0] if positional else None)
            value = _literal(bindings.value(arg)) if arg is not None else ""
            tags.add(value)
        elif (
            bindings.is_api(name, "androidx.compose.ui.semantics.semantics")
            and callback is not None
        ):
            # 只处理语义块的直接赋值，子调用或条件分支中的字段不能证明当前节点的语义。
            for stmt in _children(callback):
                left = stmt.child_by_field_name("left")
                right = stmt.child_by_field_name("right")
                if stmt.type != "assignment" or left is None or right is None:
                    continue
                # Compose 可直接向语义字段赋值。曝光 ID 的开关本身不等于标识。
                property_name = _text(left)
                if bindings.is_api(
                    property_name, "androidx.compose.ui.semantics.contentDescription"
                ):
                    if label := _label(right, bindings):
                        labels.add(label)
                elif bindings.is_api(property_name, "androidx.compose.ui.semantics.testTag"):
                    tags.add(_literal(bindings.value(right)))
        elif any(
            bindings.is_api(name, "androidx.compose.foundation." + api)
            for api in ("clickable", "combinedClickable")
        ):
            clickable = True
        elif bindings.is_api(name, "androidx.compose.ui.semantics.clearAndSetSemantics"):
            # 向更内层搜索会把被 clear 掉的标识/描述混到这条结果。
            break
        # 向左退回同一 modifier 链的接收器，参数里另一个 Widget 的声明不会被遍历进来。
        node = receiver[0]
    return tags, labels, clickable


def _bindings(root: Node, namespace: str) -> _Bindings | None:
    """建立文件作用域的 import、静态初始化和符号遮挡信息；不加载其他 Kotlin 文件。"""
    imports: dict[str, str] = {}
    wildcards: set[str] = set()
    package_name = ""
    for header in _children(root):
        fields = _children(header)
        if header.type == "package_header" and fields:
            package_name = _text(fields[0])
        if header.type != "import" or header.has_error or not fields:
            continue
        path = _text(fields[0])
        if any(child.type == "*" for child in header.children):
            wildcards.add(path)
            continue
        alias = _text(fields[1]) if len(fields) == 2 else path.rsplit(".", 1)[-1]
        if alias in imports and imports[alias] != path:
            return None
        imports[alias] = path

    definitions: Counter[str] = Counter()
    values: dict[str, tuple[Node, Node]] = {}
    for declaration in _walk(root):
        if declaration.type in ("function_declaration", "class_declaration", "object_declaration"):
            parts = _children(declaration)
            name = declaration.child_by_field_name("name")
            if name is None:
                name = next((field for field in parts if field.type == "identifier"), None)
            if name is not None:
                definitions[_text(name)] += 1
        elif declaration.type in ("parameter", "class_parameter", "variable_declaration"):
            parts = _children(declaration)
            if not parts or parts[0].type != "identifier":
                continue
            name = _text(parts[0])
            definitions[name] += 1
            parent = declaration.parent
            scope = parent.parent if parent is not None else None
            if (
                parent is None
                or parent.type != "property_declaration"
                or parent.has_error
                or scope is None
                or scope.type not in _SCOPE_TYPES
            ):
                continue
            if any(child.type == "val" for child in parent.children):
                previous = None
                for child in parent.children:
                    if previous == "=" and child.is_named:
                        values[name] = child, scope
                        break
                    if child.type not in _COMMENTS:
                        previous = child.type
    return _Bindings(package_name, namespace, imports, wildcards, definitions, values)


def _evidence(node: Node, bindings: _Bindings) -> _Evidence | None:
    """提取一次控件调用的真实标签和标识，必要时读取官方控件合并的直接子项。"""
    parsed = _call(node)
    if parsed is None:
        return None
    callee, named, positional, callback = parsed
    if callee.type != "identifier":
        return None
    # 带尾部 lambda 的调用在 Kotlin 树内有两层；只读取外层以保留子项并防止重复证据。
    parent = node.parent
    if parent is not None and parent.type == "call_expression" and _children(parent)[0] == node:
        return None
    name = _text(callee)
    tags, labels, clickable = _modifier(named.get("modifier"), bindings)
    direct_label = _same_widget_label(name, named, positional, bindings)
    if direct_label is not None:
        labels.add(direct_label)
    button = next(
        (
            symbol
            for symbol in _BUTTONS
            if any(bindings.is_api(name, f"{base}.{symbol}") for base in _MATERIAL)
        ),
        "",
    )
    # 可追踪的父子标签关联仅来自已知会合并语义的组件及其直接 Text 或 Icon。
    if button:
        clickable = True
        if label := _child_label(named.get("content") or callback, bindings):
            labels.add(label)
    if any(
        bindings.is_api(name, f"{base}.{symbol}") for base in _MATERIAL for symbol in _TEXT_FIELDS
    ):
        if label := _child_label(named.get("label") or named.get("placeholder"), bindings):
            labels.add(label)
    tag = next(iter(tags)) if len(tags) == 1 else ""
    if not (tag or labels):
        return None
    return _Evidence(
        tuple(sorted(labels, key=lambda label: (label.owner, label.key, label.literal))),
        tag,
        clickable,
    )


@lru_cache(maxsize=512)
def _read_kotlin(
    path: Path, modified: int, changed: int, size: int, namespace: str
) -> tuple[_Evidence, ...]:
    """按文件路径、mtime/ctime 纳秒和字节数解析并复用单文件证据；不跨进程持久化。

    namespace 随构建文件变化决定 R 的同包引用，文件大小竞态或超限时放弃结果。
    缓存只保留静态标签，不持有语法树。
    """
    with path.open("rb") as stream:
        data = stream.read(_MAX_FILE_BYTES + 1)
    if len(data) != size:
        raise ValueError("Kotlin source changed while reading")
    if b"androidx.compose." not in data or not any(
        marker in data
        for marker in (
            b"testTag",
            b"Text",
            b"Icon",
            b"contentDescription",
            b"semantics",
        )
    ):
        return ()
    root = Parser(_LANGUAGE).parse(data).root_node
    bindings = _bindings(root, namespace)
    if bindings is None:
        return ()
    results = []
    for node in _walk(root):
        if candidate := _evidence(node, bindings):
            results.append(candidate)
    return tuple(results)


def _values(
    modules: dict[Path, set[Path]], namespaces: dict[Path, str], keys: dict[str, set[str]]
) -> dict[str, dict[str, tuple[set[str], set[str]]]]:
    """在绑定组的命名空间内批量解析确定的 R 引用，不与同名其他模块合并。

    modules 是 Android 模块和已授权资源根，keys 按命名空间列出需要的原始资源名。
    返回命名空间、资源名到完整别名及译文，解析异常使本轮失效。
    """
    owners = Counter(namespaces.values())
    found = {}
    count = total_bytes = 0
    for module, resources in modules.items():
        namespace = namespaces[module]
        if namespace not in keys or owners[namespace] != 1:
            continue
        strings: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for resource in sorted(resources):
            for directory in _resource_dirs(resource):
                if directory.name.split("-", 1)[0] != "values":
                    continue
                for path in _entries(directory):
                    if path.suffix != ".xml" or path.is_symlink() or not path.is_file():
                        continue
                    with path.open("rb") as source:
                        data = source.read(_MAX_FILE_BYTES + 1)
                    count += 1
                    total_bytes += len(data)
                    if (
                        count > _MAX_RESOURCES
                        or len(data) > _MAX_FILE_BYTES
                        or total_bytes > _MAX_BYTES
                    ):
                        raise ValueError("Invalid or excessive Compose Android resources")
                    # 先按 Android 工程的 UTF-8 文件读取，避免其他编码绕过 DTD 检查。
                    xml = data.decode("utf-8-sig")
                    if "<!DOCTYPE" in xml:
                        raise ValueError("Unexpected DTD in Compose Android resources")
                    _record_strings(ET.fromstring(xml), directory, strings)
        found[namespace] = {key: _resolve_string(strings, key) for key in keys[namespace]}
    return found


def _kotlin_files(
    modules: dict[Path, set[Path]], roots: tuple[Path, ...]
) -> Iterator[tuple[Path, Path, os.stat_result]]:
    """枚举绑定模块下各有效 Android 源集的 Kotlin 文件；跳过链接、生成代码和测试源集。

    返回 module、path 及 stat，所有路径、文件数量和累计大小在单组内受限。
    """
    visited: set[Path] = set()
    count = total_bytes = path_count = 0
    for module in sorted(modules):
        src = module / "src"
        if not any(module.is_relative_to(root) for root in roots) or not _real_dir(src):
            continue
        for source_set in _entries(src):
            if not _real_dir(source_set) or _TEST_SOURCE.match(source_set.name):
                continue
            for source_dir in _SOURCE_DIRS:
                base = source_set / source_dir
                if not _real_dir(base):
                    continue
                for directory, subdirs, files in os.walk(
                    base, followlinks=False, onerror=_raise_walk_error
                ):
                    path_count += len(subdirs) + len(files)
                    if path_count > _MAX_PATHS:
                        raise ValueError("Compose source path count exceeds limit")
                    location = Path(directory)
                    subdirs[:] = sorted(
                        part
                        for part in subdirs
                        if not part.startswith(".") and not (location / part).is_symlink()
                    )
                    for filename in sorted(files):
                        if not filename.endswith(".kt"):
                            continue
                        path = location / filename
                        if path in visited or path.is_symlink() or not path.is_file():
                            continue
                        visited.add(path)
                        info = path.stat()
                        count += 1
                        total_bytes += info.st_size
                        if (
                            count > _MAX_FILES
                            or total_bytes > _MAX_BYTES
                            or info.st_size > _MAX_FILE_BYTES
                        ):
                            raise ValueError("Compose Kotlin sources exceed scan budget")
                        yield module, path, info


def _raise_walk_error(error: OSError) -> None:
    """中止读取不完整目录，避免只看见一个候选就误认为可唯一定位。"""
    raise error


def _read_compose(roots: tuple[Path, ...], targets: tuple[str, ...]) -> dict[str, list[SourceHint]]:
    """扫描一次绑定组，按查询汇集静态 Compose 证据，实际控件仍须在当前 Android 帧确认。"""
    modules = _modules(roots, include_empty=True)
    # 显式资源引用保留准确键名；自然语言目标只在文案路径中做归一化。
    wanted = {target: normalize_text(target) for target in targets}
    explicit_keys: dict[str, str] = {}
    for target in targets:
        name = target.strip()
        for prefix in ("R.string.", "@string/"):
            if name.startswith(prefix):
                explicit_keys[target] = name.removeprefix(prefix)
                break
    result: dict[str, list[SourceHint]] = {target: [] for target in targets}
    # 当原始查询恰好等于声明的 Compose ID，保留完整 ID 证据，避免同名显示文案参与决策。
    exact_ids: dict[str, list[SourceHint]] = defaultdict(list)
    namespaces: dict[Path, str] = {}
    uses: list[tuple[Path, Path, _Evidence]] = []
    keys: dict[str, set[str]] = defaultdict(set)
    for module, path, info in _kotlin_files(modules, roots):
        if module not in namespaces:
            namespaces[module] = _local_r_namespace(module, roots)
        for evidence in _read_kotlin(
            path, info.st_mtime_ns, info.st_ctime_ns, info.st_size, namespaces[module]
        ):
            uses.append((module, path, evidence))
            for label in evidence.labels:
                if label.key:
                    keys[label.owner].add(label.key)

    # Kotlin-only 的功能模块也可以引用同组只放翻译的 Android 模块。
    resources = {}
    if keys:
        for module in modules:
            if module not in namespaces:
                namespaces[module] = _local_r_namespace(module, roots)
        resources = _values(modules, namespaces, keys)

    # 至此已拿到一批静态声明与译文。每条证据独立归入查询，避免混用不同控件的 ID 和文案。
    for module, path, evidence in uses:
        # 资源键与普通文案分开。查询显式资源表达式时不能用刚好同名的固定文字命中。
        resource_keys: set[str] = set()
        labels = {item.literal for item in evidence.labels if item.literal}
        for label in evidence.labels:
            linked, translated = resources.get(label.owner, {}).get(label.key, (set(), set()))
            resource_keys.update(linked)
            labels.update(translated)
        aliases = {normalize_text(label) for label in labels | resource_keys}
        for target, needle in wanted.items():
            if evidence.tag and target.strip() == evidence.tag:
                exact_ids[target].append(
                    SourceHint(
                        resource_id=evidence.tag,
                        origin=str(path.relative_to(module)),
                        action="tap" if evidence.clickable else "",
                    )
                )
                continue
            resource = explicit_keys.get(target)
            matches = resource in resource_keys if resource is not None else needle in aliases
            if matches:
                result[target].append(
                    SourceHint(
                        labels=tuple(sorted(labels)),
                        resource_id=evidence.tag,
                        origin=str(path.relative_to(module)),
                        action="tap" if evidence.clickable else "",
                    )
                )
    result.update(exact_ids)
    return result


def read_compose_many(
    roots: tuple[Path, ...], targets: tuple[str, ...]
) -> dict[str, list[SourceHint]]:
    """为同批非空 targets 读取绑定的规范化绝对工程 roots，结果按原样目标分组。

    每个候选只使用语法证实的标签和原始 testTag。原生树只有在 Compose 暴露该 tag 后
    才能按 ID 匹配；未暴露时只可能用同一控件的真实文字。函数同步只读，可在现有工作
    线程调用。读盘、解析资源及预算错误安全回退，本地工作台不会执行第三方工程。
    """
    distinct = tuple(dict.fromkeys(target for target in targets if normalize_text(target)))
    try:
        return _read_compose(roots, distinct) if distinct else {}
    except (OSError, UnicodeError, ValueError, ET.ParseError, RuntimeError):
        return {target: [] for target in distinct}
