"""从 Android Views 的声明式布局提取本机控件线索，由调用方在设备上核验。

```text
绑定目录、待查名称
  ↓
发现 Gradle 模块和直接本地依赖，读取本批 XML
  ├─ values 收集各语言的字符串及 @string 引用
  └─ layout / menu 从同一节点提取 ID、文字、描述和点击声明
  ↓
布局变体取共同声明，模块文案优先于直接依赖的同语言文案
  ├─ 查 ID：匹配资源项名称，保留查询中显式提供的包名前缀
  └─ 查文字：跟随字符串引用，核对资源键或最终译文
       └─ ID 被不同控件语义复用：只保留文案用于真机核对
  ↓
返回「原查询 → 源码候选」，由 screen.py 确认当前控件
```

字符串链缺失、有环或冲突时，跳过这条文案线索。读取或解析失败、超出扫描预算时，
本批 Android Views 查询返回空候选。模块发现不运行 Gradle，资源读取不触发点击。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from artemis.agents.explorer.screen_index import normalize_text

from artemis.utils.source_locating.android.resources import (
    _entries,
    _local_r_namespace,
    _MAX_FILE_BYTES,
    _modules,
    _plain,
    _record_strings,
    _resource_dirs,
    _resolve_string,
    _STRING_REF,
    _Strings,
    _UI_RESOURCE_TYPES,
)
from artemis.utils.source_locating.common.types import SourceHint

# ElementTree 展开的 Android XML 命名空间。忽略 tools 命名空间中的编辑器预览值。
_ANDROID = "{http://schemas.android.com/apk/res/android}"
# Android 声明中的本地 View ID，只接受完整的 @id/ 或 @+id/，保留名称大小写。
_VIEW_ID = re.compile(r"@\+?id/([a-zA-Z_][a-zA-Z0-9_]*)")
# 允许用户直接提供资源表达式、R.id 引用，或者设备实际暴露的包名限定 ID。
_QUERY_ID = re.compile(r"(?:@\+?id/|R\.id\.|(?:[A-Za-z][\w.]*):id/)([A-Za-z_]\w*)", re.ASCII)
# 支持 Data Binding 的隐式 Context 读取单个字符串常量，额外参数、其他接收者和动态键不匹配。
_GET_STRING = re.compile(
    r"context\s*\.\s*getString\s*\(\s*R\s*\.\s*string\s*\.\s*([A-Za-z_]\w*)\s*\)", re.ASCII
)
# Android 表单只有在没有显式文字时才采用 hint 作为输入提示。仅识别 framework 已知输入类。
_INPUT_VIEWS = {"EditText", "AutoCompleteTextView", "MultiAutoCompleteTextView"}
# 每批允许读取的 XML 文件数和累计字节数；超限后整批交回原定位链。
_MAX_FILES = 512
_MAX_TOTAL_BYTES = 32 * 1024 * 1024


# 每条声明依次包含 View ID、本节点的 text 和 contentDescription、可静态证实的动作。
_Declaration = tuple[str, str, str, str]
# 同名布局按真实 XML 文件区分变体；字符串按资源键、语言和静态值组织。
_Layouts = dict[str, dict[Path, set[_Declaration]]]


def _imports_local_r(document: ET.Element, namespace: str) -> bool:
    """仅当布局明确导入该模块的 R 且 context/R 没有被同名变量和视图遮蔽时允许解析方法。"""
    if document.tag != "layout" or not namespace:
        return False
    # 导入别名也是表达式命名空间的一部分，例如其他包的类被重命名为 R。
    owners = []
    for item in document.findall("data/*"):
        if item.tag == "variable" and item.get("name") in {"R", "context"}:
            return False
        if item.tag == "import":
            owner = item.get("type", "")
            name = item.get("alias") or owner.rsplit(".", 1)[-1]
            if name == "context":
                return False
            if name == "R":
                owners.append(owner)
    if owners != [namespace + ".R"]:
        return False
    return not any(
        view.get(_ANDROID + "id", "").rsplit("/", 1)[-1] in {"R", "context"}
        for view in document.iter()
    )


def _layout_value(value: str, binding_layout: bool, local_r: bool) -> str:
    """返回声明属性的值，仅在 Data Binding 布局中将单个静态字符串表达式还原为资源引用。"""
    if binding_layout and value.startswith("@{") and value.endswith("}"):
        # 保留条件分支、格式参数及方法调用的原式，后续不能将它们当作静态标签。
        expression = value[2:-1].strip()
        if _STRING_REF.fullmatch(expression):
            return expression
        if local_r and (getter := _GET_STRING.fullmatch(expression)):
            return "@string/" + getter[1]
    return value


def _declarations(document: ET.Element, namespace: str) -> set[_Declaration]:
    """返回布局 document 内各 View 自身声明的 ID 和标签，避免把兄弟节点文案配给图标。"""
    declarations: set[_Declaration] = set()
    # Android Data Binding 的源码布局由 layout 根元素标识。
    binding_layout = document.tag == "layout"
    # 只有 R 命名空间可证实时才解读 Context.getString()，纯 @string 引用沿用资源自身的模块。
    local_r = _imports_local_r(document, namespace)
    for view in document.iter():
        # ID 只接受本控件声明或引用的 id 资源，R.string 永远不会转换成 View ID。
        raw_id = _VIEW_ID.fullmatch(view.get(_ANDROID + "id", ""))
        # android:onClick 声明的具体方法可以区分可点行和标题；不猜测数据绑定表达式。
        callback = view.get(_ANDROID + "onClick", "")
        # 有固定文字的输入框不展示提示。非输入控件也不能用同名的自定义属性推断可见标签。
        text = view.get(_ANDROID + "text", "")
        if document.tag == "menu" and view.tag == "item":
            # 菜单项的可访问名称取自 title，具体动作仍由当前的可点击节点证实。
            text = view.get(_ANDROID + "title", "")
        if not text and view.tag.removeprefix("android.widget.") in _INPUT_VIEWS:
            text = view.get(_ANDROID + "hint", "")
        declarations.add(
            (
                raw_id[1] if raw_id else "",
                _layout_value(text, binding_layout, local_r),
                _layout_value(
                    view.get(_ANDROID + "contentDescription", ""), binding_layout, local_r
                ),
                "tap" if re.fullmatch(r"[A-Za-z_$][\w$]*", callback) else "",
            )
        )
    return declarations


def _module_hints(
    layouts: _Layouts,
    strings: _Strings,
    target: str,
) -> list[SourceHint]:
    """仅从同一模块、同一个 View 的声明产生线索；布局和字符串的变体分歧保守回退。

    layouts 按文件名汇集不同 sourceSet/设备方向的声明，strings 按键和语言保存静态值。
    target 是当前查找词。返回候选只含静态标签和可选 View ID，调用方仍需核验当前树。
    """
    # 对 ID 保持大小写和包名前缀，对用户界面文案沿用现有空白及大小写规范化。
    wanted = normalize_text(target)
    explicit_id = _QUERY_ID.fullmatch(target.strip())
    # 显式字符串引用只比对该键，不能因另一控件的显示文字刚好等于键名而选择后者。
    string_ref = re.fullmatch(r"(?:@string/|R\.string\.)([A-Za-z_]\w*)", target.strip(), re.ASCII)
    hints: list[SourceHint] = []
    for versions in layouts.values():
        # 相同布局的多个版本只能使用共同出现的控件声明，防止猜测当前构建或屏幕方向。
        for view_id, text, description, action in sorted(set.intersection(*versions.values())):
            labels: set[str] = set()
            keys: set[str] = set()
            for value in (text, description):
                if reference := _STRING_REF.fullmatch(value):
                    # 文案根键、中间别名和最终资源只关联当前布局节点。
                    linked_keys, translations = _resolve_string(strings, reference[1])
                    keys.update(linked_keys)
                    labels.update(translations)
                elif _plain(value):
                    labels.add(value)
            if explicit_id:
                if explicit_id[1] != view_id:
                    continue
                # 对用户显式给出的 ID 用 ID 自身核验，包名前缀应原样保留给真机比较。
                actual_id = target.strip() if ":id/" in target else view_id
                labels.clear()
            elif string_ref and string_ref[1] in keys:
                actual_id = view_id
            elif not string_ref and any(normalize_text(value) == wanted for value in labels | keys):
                actual_id = view_id
            else:
                continue
            hints.append(
                SourceHint(
                    labels=tuple(sorted(labels)),
                    resource_id=actual_id,
                    android_id=bool(actual_id),
                    origin=str(next(iter(versions))),
                    action=action,
                )
            )
    return hints


def _read_android(roots: tuple[Path, ...], targets: tuple[str, ...]) -> dict[str, list[SourceHint]]:
    """在统一读盘预算内同步解析所有绑定模块，返回静态候选；I/O、格式及预算异常由公开入口处理。"""
    # 多个原始目标共用同一份资源快照；结果仍按调用方给出的查找词独立归类。
    hints: dict[str, list[SourceHint]] = {target: [] for target in targets}
    # 两个计数分别限制本轮读取的 XML 个数及总字节数，所有模块共享预算。
    file_count = byte_count = 0
    # 相同 View ID 在另一页或模块可能用于完全不同的控件；有冲突时只使用文案线索。
    uses: dict[str, set[tuple[Path, str, str]]] = defaultdict(set)
    # 构建元数据只确定声明了依赖的模块；每个模块的语言表单独保留，以便发现冲突。
    libraries: dict[Path, set[Path]] = {}
    modules = _modules(roots, libraries=libraries)
    # 只提供 strings 的库也要参与读取；没有布局、菜单或依赖关系的模块无需扫描 XML。
    shared_modules = set().union(*libraries.values())
    # 先收集所有模块的 XML，再按依赖合并字符串，结果不依赖磁盘遍历的先后顺序。
    module_strings: dict[Path, _Strings] = {}
    module_layouts: dict[Path, _Layouts] = {}
    for module, resources in modules.items():
        # layout 名称在同一模块内代表同一个可被 build variant/设备配置替换的资源。
        layouts: _Layouts = defaultdict(dict)
        # 保留资源类型和语言维度。任意 values XML 文件中均可包含 string 声明。
        strings: _Strings = defaultdict(lambda: defaultdict(set))
        # 懒读当前模块声明；大多数普通 XML 布局只使用 @string，无需触碰构建元数据。
        namespace: str | None = None
        directories = [
            directory for resource in sorted(resources) for directory in _resource_dirs(resource)
        ]
        if module not in shared_modules and not any(
            directory.name.split("-")[0] in _UI_RESOURCE_TYPES for directory in directories
        ):
            continue
        for directory in directories:
            for path in _entries(directory):
                if path.suffix != ".xml" or path.is_symlink() or not path.is_file():
                    continue
                file_count += 1
                if file_count > _MAX_FILES:
                    raise ValueError("Too many Android resources")
                with path.open("rb") as stream:
                    data = stream.read(_MAX_FILE_BYTES + 1)
                byte_count += len(data)
                if len(data) > _MAX_FILE_BYTES or byte_count > _MAX_TOTAL_BYTES:
                    raise ValueError("Android resources exceed byte budget")
                # Android 资源使用无 DTD 的 XML；在解析前拒绝实体声明，解析器不访问外部文件。
                xml = data.decode("utf-8-sig")
                if "<!DOCTYPE" in xml:
                    raise ValueError("DTD is not an Android resource declaration")
                document = ET.fromstring(xml)
                if directory.name.split("-")[0] == "values":
                    _record_strings(document, directory, strings)
                else:
                    # 保存每个版本的真实来源；同一布局的不同方向和构建配置会在匹配前取交集。
                    if namespace is None and document.tag == "layout" and "getString" in xml:
                        namespace = _local_r_namespace(module, roots)
                    declarations = _declarations(document, namespace or "")
                    # 菜单、布局可以同名，必须按资源类型分别判断配置变体。
                    kind = directory.name.split("-", 1)[0]
                    layouts[f"{kind}/{path.name}"][path] = declarations
                    for view_id, text, description, _action in declarations:
                        if view_id:
                            uses[view_id].add((module, text, description))
        module_strings[module] = strings
        if layouts:
            module_layouts[module] = layouts
    for module, layouts in module_layouts.items():
        # 本地模块可覆盖依赖的同一语言。两个依赖对同一值存在冲突时保留两个值，让解析回退。
        strings: _Strings = defaultdict(lambda: defaultdict(set))
        for library in libraries.get(module, set()):
            for key, variants in module_strings.get(library, {}).items():
                for locale, values in variants.items():
                    strings[key][locale].update(values)
        for key, variants in module_strings[module].items():
            # 本地值覆盖依赖的对应限定符；其他语言仍来自已声明的依赖。
            strings[key].update(variants)
        for target in targets:
            hints[target].extend(_module_hints(layouts, strings, target))
    # 空 labels 代表用户明确查询了 ID，只需要设备上该 ID 唯一；文案查询则必须排除复用歧义。
    return {
        target: [
            replace(hint, resource_id="", android_id=False)
            if hint.labels and hint.resource_id and len(uses[hint.resource_id]) > 1
            else hint
            for hint in matches
        ]
        for target, matches in hints.items()
    }


def read_android_many(
    roots: tuple[Path, ...], targets: tuple[str, ...]
) -> dict[str, list[SourceHint]]:
    """一次扫描 roots，按各个非空查找词返回线索；原词及剥离角色后的目标共用同一份 XML。

    roots 是绑定的本机绝对目录，targets 内相同的查找词按首次顺序去重。
    资源读取或解析失败时返回空结果。文件句柄在返回前关闭，下一批重新读取，允许源码变更。
    """
    # 保留原始大小写、包名及资源名；规范化结果只用于过滤空查询。
    distinct = tuple(dict.fromkeys(t for t in targets if normalize_text(t)))
    try:
        return _read_android(roots, distinct) if distinct else {}
    except (OSError, UnicodeError, ValueError, ET.ParseError, RuntimeError):
        return {target: [] for target in distinct}


def read_android(roots: tuple[Path, ...], target: str) -> list[SourceHint]:
    """从 roots 的 Android Views XML 查 target；只读线程中调用，目录失效或超预算即返回空列表。

    本函数不运行 Gradle，也不推导当前设备坐标。返回的资源 ID 与标签仅供运行时匹配，
    每条关联来自同一控件声明。无法静态解析的资源引用不会产生文案证据。
    """
    return read_android_many(roots, (target,)).get(target, [])
