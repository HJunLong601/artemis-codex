"""用 TSX 语法树核对 React Native 源码的单控件属性及导入绑定。

```text
单个文件的语法树
  → 从 import 核对控件真正来自哪个库，排除类型导入和被同名符号遮蔽的项
  → 找到 JSX 控件本身的开始标签，跳过能确定不会渲染的分支
  → 按书写顺序读取属性，遇到 {...props} 就移除之前可能被覆盖的证据
  → 同一控件的原始 testID / 无障碍文案 / 标准组件标签
  └─ 文字来自动态值或归属不明 → 放弃该条证据
```

例如 `<Pressable testID="inbox" onPress={open}><Text>收件箱</Text></Pressable>`
会关联「收件箱」和 `inbox`。如果这个 Text 自己有独立的点击行为，就不会把它的文字
借给外层控件。语法树证据仍需和设备运行时的属性吻合。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from html import unescape

from tree_sitter import Language, Node
from tree_sitter_typescript import language_tsx

from artemis.utils.source_locating.common.types import SourceHint

# TSX 语法也涵盖普通 JavaScript 里的 JSX。Parser 按文件创建，线程之间只共享不可变语法对象。
TSX = Language(language_tsx())
# 普通开闭标签和自闭合标签都可能对应设备上的控件。
JSX_TYPES = {"jsx_element", "jsx_self_closing_element"}
# 这些组件由 react-native 提供。没有显式导入的同名组件及业务包装器不按官方属性推断。
_NATIVE = {
    "View",
    "Text",
    "Button",
    "Pressable",
    "TouchableHighlight",
    "TouchableOpacity",
    "TouchableWithoutFeedback",
    "TouchableNativeFeedback",
    "TextInput",
    "Switch",
    "Image",
    "ScrollView",
    "FlatList",
    "SectionList",
}
# 仅能按这些 React Native 组件的约定将唯一的直接 Text 子项与点击控件关联。
_TOUCHABLES = {
    "Pressable",
    "TouchableHighlight",
    "TouchableOpacity",
    "TouchableWithoutFeedback",
    "TouchableNativeFeedback",
}


def node_text(node: Node | None) -> str:
    """将语法节点原始 UTF-8 字节读为字符串；缺少节点返回空串，解码失败由批次处理。"""
    return (node.text or b"").decode("utf-8") if node is not None else ""


def walk_nodes(root: Node) -> Iterator[Node]:
    """按源码顺序遍历具名语法节点；迭代而非递归，适用于深嵌套 JSX。"""
    # 栈使用逆序子节点，以便第一个子节点最先弹出。
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.named_children))


def expression(node: Node | None) -> Node | None:
    """解开只含一个值的 JSX/括号表达式；注释与空容器不代表值。"""
    while node is not None and node.type in {"jsx_expression", "parenthesized_expression"}:
        values = [child for child in node.named_children if child.type != "comment"]
        node = values[0] if len(values) == 1 else None
    return node


def static_value(node: Node | None, constants: dict[str, str] | None = None) -> str:
    """读取单个静态字符串，支持单/双引号、无插值模板以及已验证的模块级不可变量。"""
    # JSX 属性的直接引号值解码 HTML 实体。花括号里的 JS 字符串保留实体原文。
    jsx_literal = (
        node is not None
        and node.type == "string"
        and bool(node.parent and node.parent.type == "jsx_attribute")
    )
    node = expression(node)
    if node is None:
        return ""
    if node.type == "identifier" and constants is not None:
        return constants.get(node_text(node), "")
    # 动态模板及 JS 转义需执行 JS 规则解释。这里保留能按原字节确认的字面表达式。
    if node.type not in {"string", "template_string"} or any(
        part.type not in {"string_fragment", "html_character_reference"}
        for part in node.named_children
    ):
        return ""
    value = node_text(node)[1:-1]
    value = unescape(value) if jsx_literal else value
    # 避免将长正文或含控制字符的字符串作为界面控件名称或 ID。
    return (
        value
        if value.strip() and len(value) <= 256 and not any(ord(ch) < 32 for ch in value)
        else ""
    )


def _binding_names(node: Node | None) -> set[str]:
    """收集绑定位置可能声明的名称；遇到解构采用宽松收集，以便保守排除遮蔽。"""
    if node is None:
        return set()
    return {
        node_text(part)
        for part in walk_nodes(node)
        if part.type in {"identifier", "shorthand_property_identifier_pattern"}
    }


def bindings(root: Node) -> tuple[dict[str, str], dict[str, str], dict[str, tuple[str, str]]]:
    """从单文件语法树 root 取得三种不重名的绑定表。

    返回本地名至原生组件名、本地名至静态常量值、本地名至外部库和导出名。外部库只是
    后续核验包装器的线索。这里静态读语法树，不执行模块或跟踪任意运行时赋值。
    """
    # 导入表保留源组件名，支持例如 import { Pressable as Tap } 的使用位置。
    imports: dict[str, str] = {}
    # 所有模块级导入别名计入重名检查，另一个库的同名声明不能继承 React Native 语义。
    declarations: Counter[str] = Counter()
    # 只有完整静态字符串的顶层 const 才会登记为可替换的标签或 testID。
    constants: dict[str, str] = {}
    # 其他直接命名导入只记录来源，由包装器解析阶段验证真实透传后才可以使用。
    external: dict[str, tuple[str, str]] = {}
    for node in walk_nodes(root):
        if node.type == "import_statement":
            library = static_value(node.child_by_field_name("source"))
            from_rn = library == "react-native"
            type_only = any(part.type == "type" for part in node.children)
            for item in walk_nodes(node):
                if item.type == "import_specifier":
                    name = node_text(item.child_by_field_name("name"))
                    alias = node_text(item.child_by_field_name("alias")) or name
                    declarations[alias] += 1
                    if type_only or any(part.type == "type" for part in item.children):
                        continue
                    if from_rn and name in _NATIVE:
                        imports[alias] = name
                    elif library and not from_rn:
                        external[alias] = (library, name)
                elif item.type == "namespace_import":
                    alias = node_text(item.named_children[-1])
                    declarations[alias] += 1
                    if from_rn and not type_only:
                        # 空值记录命名空间导入，使用时还需检查具体成员。
                        imports[alias] = ""
                elif item.type == "import_clause":
                    declarations.update(
                        node_text(part) for part in item.named_children if part.type == "identifier"
                    )
        elif node.type == "variable_declarator":
            binding = node.child_by_field_name("name")
            declarations.update(_binding_names(binding))
            parent = node.parent
            # 只有模块级 const 能跨组件传入字段，函数中的常量和同名遮蔽保守跳过。
            if (
                parent
                and parent.type == "lexical_declaration"
                and parent.children[0].type == "const"
            ):
                block = parent.parent
                if block and block.type == "export_statement":
                    block = block.parent
                if block and block.type == "program" and binding and binding.type == "identifier":
                    literal = static_value(node.child_by_field_name("value"))
                    if literal:
                        constants[node_text(binding)] = literal
        elif node.type in {
            "function_declaration",
            "function_expression",
            "class_declaration",
            "class",
            "generator_function",
            "generator_function_declaration",
            "enum_declaration",
            "internal_module",
        }:
            declarations.update(_binding_names(node.child_by_field_name("name")))
        elif node.type in {
            "formal_parameters",
            "catch_clause",
            "arrow_function",
            "for_in_statement",
        }:
            if node.type == "formal_parameters":
                declarations.update(_binding_names(node))
            else:
                declarations.update(
                    _binding_names(
                        node.child_by_field_name(
                            "parameter" if node.type != "for_in_statement" else "left"
                        )
                    )
                )
    return (
        {alias: kind for alias, kind in imports.items() if declarations[alias] == 1},
        {name: label for name, label in constants.items() if declarations[name] == 1},
        {alias: source for alias, source in external.items() if declarations[alias] == 1},
    )


def element_open_tag(element: Node) -> Node | None:
    """返回完整 JSX 元素的起始标签或独立自闭合节点；有语法错误的控件不参与定位。"""
    if element.has_error:
        return None
    return element.child_by_field_name("open_tag") if element.type == "jsx_element" else element


def element_reachable(element: Node) -> bool:
    """排除能从字面布尔值确定的死分支；其他条件仍由当前设备的可见性核验。"""
    # 沿祖先往上找分支条件，branch 始终是 parent 直接包住的语法节点。
    branch = element
    parent = branch.parent
    while parent is not None:
        if parent.type in {"if_statement", "ternary_expression"}:
            condition = expression(parent.child_by_field_name("condition"))
            if condition is not None and condition.type in {"true", "false"}:
                skipped = "alternative" if condition.type == "true" else "consequence"
                if parent.child_by_field_name(skipped) == branch:
                    return False
        elif parent.type == "binary_expression" and parent.child_by_field_name("right") == branch:
            left = expression(parent.child_by_field_name("left"))
            operator = node_text(parent.child_by_field_name("operator"))
            if (operator, left.type if left is not None else "") in {
                ("&&", "false"),
                ("||", "true"),
            }:
                return False
        branch, parent = parent, parent.parent
    return True


def element_kind(tag: Node, imports: dict[str, str]) -> str:
    """把 JSX 名称映射到 react-native 实际导入；别名和静态命名空间成员均可识别。"""
    name = tag.child_by_field_name("name")
    if name is None:
        return ""
    if name.type == "identifier":
        return imports.get(node_text(name), "")
    if (
        name.type == "member_expression"
        and imports.get(node_text(name.child_by_field_name("object"))) == ""
    ):
        member = node_text(name.child_by_field_name("property"))
        return member if member in _NATIVE else ""
    return ""


def element_props(tag: Node) -> tuple[dict[str, Node | None], bool]:
    """按 JSX 真实顺序收集属性。遇到动态展开时清空前面的静态证据，后续显式属性可覆盖。"""
    # 值为 None 表示属性没有显式值。has_spread 记录本标签是否可能还有未知的动态属性。
    props: dict[str, Node | None] = {}
    has_spread = False
    for attr in tag.children_by_field_name("attribute"):
        if attr.type != "jsx_attribute":
            # 任意动态展开可以改变标识、可读文本和点击回调。
            props.clear()
            has_spread = True
            continue
        parts = [part for part in attr.named_children if part.type != "comment"]
        if parts:
            props[node_text(parts[0])] = parts[1] if len(parts) == 2 else None
    return props, has_spread


def element_children(element: Node) -> list[Node]:
    """取得 JSX 直接子项，省略格式空白、注释和首尾标签；保留所有实际输出表达式。"""
    return (
        [
            child
            for child in element.named_children
            if child.type not in {"jsx_opening_element", "jsx_closing_element", "comment"}
            and (child.type != "jsx_text" or node_text(child).strip())
            and (child.type != "jsx_expression" or expression(child) is not None)
        ]
        if element.type == "jsx_element"
        else []
    )


def _text_child(element: Node, constants: dict[str, str]) -> str:
    """只读取 Text 自身完全静态的内容；多段动态文本或独立 JSX 容器留给运行时。"""
    # 保存文字片段的原始顺序，避免把同一行多段文本分成不同候选名称。
    pieces = []
    for child in element_children(element):
        if child.type in {"jsx_text", "html_character_reference"}:
            pieces.append(unescape(node_text(child)))
        elif child.type == "jsx_expression" and (value := static_value(child, constants)):
            pieces.append(value)
        else:
            return ""
    # JSX 源格式中的换行不用于区分 UI 文字；屏幕侧做同样的空白规范化。
    text = " ".join("".join(pieces).split())
    return text if len(text) <= 256 else ""


def hint_for_element(
    element: Node,
    tag: Node,
    kind: str,
    imports: dict[str, str],
    constants: dict[str, str],
    origin: str,
) -> SourceHint | None:
    """从 JSX element 的起始 tag 提取可核查的 ID 和标签。

    kind 是已确认的框架组件名或调用方待核验的 Pressable 包装器假设。imports 用于
    核对直接子节点的真实组件类型，constants 用于解析同文件静态值，origin 只记源码
    文件和行号。没有同一节点上的静态证据则返回 None，结果不含坐标。
    """
    props, spread = element_props(tag)
    children = element_children(element)
    if kind in {"TouchableWithoutFeedback", "TouchableNativeFeedback"}:
        # 这两个组件复用单一子节点作为承载 View。未知组件能否透传属性不可静态保证。
        native = (
            element_open_tag(children[0])
            if len(children) == 1 and children[0].type in JSX_TYPES
            else None
        )
        if native is None or element_kind(native, imports) not in {"View", "Text"}:
            return None
    # React Native 在 Android 上原样暴露 testID。标题可有多个同源表达，不从文字生成 ID。
    resource = static_value(props.get("testID"), constants)
    labels = []
    # aria-label 优先于 accessibilityLabel。动态覆盖项不能用较早的标签背书。
    name = props.get("aria-label") if "aria-label" in props else props.get("accessibilityLabel")
    if label := static_value(name, constants):
        labels.append(label)
    if kind in {"Button", "TextInput"}:
        # Button 的 title 为标准可见文字；TextInput 在空值时显示 placeholder。
        if label := static_value(
            props.get("title" if kind == "Button" else "placeholder"), constants
        ):
            labels.append(label)
    elif kind == "Text" and "children" not in props and (label := _text_child(element, constants)):
        labels.append(label)
    elif kind in _TOUCHABLES and len(children) == 1 and children[0].type == "jsx_element":
        # 仅连接唯一、没有独立交互/无障碍属性的直接 Text 子项，绝不借用邻近文字。
        child = children[0]
        if (child_tag := element_open_tag(child)) is not None and element_kind(
            child_tag, imports
        ) == "Text":
            child_props, child_spread = element_props(child_tag)
            if not child_spread and not {
                "testID",
                "nativeID",
                "accessibilityLabel",
                "accessibilityRole",
                "aria-label",
                "accessible",
                "accessibilityElementsHidden",
                "onPress",
                "onLongPress",
                "children",
            }.intersection(child_props):
                if label := _text_child(child, constants):
                    labels.append(label)
    # 展开项可能同时提供 aria-label；只有后置静态 ID 或显式标签能作为来源线索。
    if spread and not resource and "aria-label" not in props:
        return None
    action = expression(props.get("onPress"))
    # disabled/可见性由设备树复核；null/undefined 不能作为源码声明了动作的证据。
    tappable = action is not None and node_text(action) not in {"null", "undefined", "false"}
    if not resource and (not labels or not tappable and kind not in {"TextInput", "Switch"}):
        return None
    return SourceHint(
        labels=tuple(dict.fromkeys(labels)),
        resource_id=resource,
        origin=origin,
        action="tap" if tappable else "",
    )
