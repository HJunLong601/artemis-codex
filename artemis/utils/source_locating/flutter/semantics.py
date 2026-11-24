"""从同一 Flutter 构造调用读取语义标识、标签和显式点击回调，将使用位置留给真机核验。

入口 declarations 接收一个绑定模块、本批查找的翻译键和文本。此层负责确认某一段
Dart 声明的证据归属，翻译表和最终位置由它的调用方处理。

```text
绑定模块的 lib 目录 + 本批查找词
  → 枚举普通 Dart 文件、核对大小和签名
  → 按标识、资源键或文案初筛
  → 分词并确认导入、最近的变量绑定和同一次构造调用的参数
  ├─ Semantics 有静态 identifier → 保留设备可观察的原始标识
  ├─ 可点控件有可追溯的 Text、label 或 tooltip → 保留标签或翻译引用
  └─ 动态调用、同名遮蔽或不同构造调用里的值 → 省略未确认的证据
```

返回项同时携带源文件，便于上层按模块解引用生成类、生成键和本地化资源。
"""

from collections.abc import Iterator
from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import re
from stat import S_ISREG

from artemis.utils.source_locating.flutter.dart_syntax import arguments, string_literal, tokenize
from artemis.utils.source_locating.flutter.json_assets import EASY_LOCALIZATION_LIBRARY


# 只解析应用库中的 Dart 源文件，防止单个巨型或生成目录拖慢普通定位。
_MAX_DART_FILES = 8192
_MAX_DART_BYTES = 128 * 1024 * 1024
# 单个源文件上限以字节计；超限时放弃该模块的源码 ID，但 ARB 仍可独立提供静态文案。
_MAX_FILE_BYTES = 2 * 1024 * 1024
# 仅接纳框架直接导入的无前缀 Semantics，未知别名/自定义同名组件留给正常定位。
_FRAMEWORK_LIBRARIES = {
    "package:flutter/widgets.dart",
    "package:flutter/material.dart",
    "package:flutter/cupertino.dart",
    "package:material_ui/material_ui.dart",
    "package:cupertino_ui/cupertino_ui.dart",
}
# Material 官方按钮把直接的 child（.icon 构造器中为 label）用作按钮名称。
_MATERIAL_BUTTONS = {"ElevatedButton", "FilledButton", "OutlinedButton", "TextButton"}


@dataclass(frozen=True)
class Declaration:
    """单个构造调用的控件特征；label 和 resource_key/owner 两种标签来源互斥。"""

    # Flutter 按原样暴露的 identifier，大小写和标点都必须保留。
    identifier: str
    # label 参数若直接使用字符串，这里保留源文案；否则为空。
    label: str
    # label 使用 GeneratedClass.of(context).key 时，这两项记录类名和 getter。
    owner: str
    resource_key: str
    # 只有同一构造调用里明确绑定了匿名点击回调才标记为 tap。
    action: str
    # 记录具体声明的构造类名供诊断；不会依据项目自定义类名推断角色。
    widget: str
    # 生成常量引用 Class.member；空串表示不需要沿调用文件的导入解引用。
    key_constant: str = ""


# 顶层命名参数中的简单本地化 getter。此处只筛文件；命中后仍以 Dart 词法结果核对参数层级。
_GETTER = re.compile(
    rb"\b(?:title|label|child|tooltip)\s*:\s*(?:const\s+)?(?:Text\s*\(\s*)?"
    rb"(?:\w+\s*\.\s*of\s*\(\s*\w+\s*\)\s*!?|\w+\s*\.\s*current|\w+)\s*\.\s*(\w+)"
)
# 字面按钮名也进入文件初筛，词法解析随后排除注释、插值、并非该控件的字段等情况。
_LITERAL_ARGUMENT = re.compile(
    rb"""\b(?:title|label|child|tooltip)\s*:\s*(?:const\s+)?(?:Text\s*\(\s*)?(['"])([^'"\\$\r\n]{1,1024})\1"""
)
# 不执行扩展方法，只将无转义、无插值且不带实参的 tr() 字符串作为潜在资源键。
_TR_KEY = re.compile(rb"""(['"])([^'"\\$\r\n]{1,1024})\1\s*\.\s*tr\s*\(\s*\)""")


def _bytes(path: Path, size: int) -> bytes:
    """最多读取一个预检过大小的文件；期间发生长度变化时让调用方放弃该轮索引。"""
    with path.open("rb") as handle:
        data = handle.read(_MAX_FILE_BYTES + 1)
    if len(data) != size:
        raise ValueError("Dart source changed during lookup")
    return data


@lru_cache(maxsize=_MAX_DART_FILES)
def _candidates(path: Path, modified: int, changed: int, size: int) -> tuple[bool, frozenset[str]]:
    """按文件签名缓存是否可能含语义 ID，以及可点构造调用可能引用的文案键。

    只缓存布尔值和键名，注释中的词也可能被初筛命中，不会直接产生定位证据。
    """
    data = _bytes(path, size)
    semantic = b"Semantics" in data and b"identifier" in data
    # 此集合同时包含资源 getter 与固定文案；仅用于缩小待分词的文件，不产生落点。
    keys = set()
    if b"onTap" in data or b"onPressed" in data:
        keys.update(key.decode("ascii") for key in _GETTER.findall(data))
        keys.update(
            " ".join(label.decode("utf-8").casefold().split())
            for _, label in _LITERAL_ARGUMENT.findall(data)
        )
        if EASY_LOCALIZATION_LIBRARY.encode() in data:
            # JSON 的键区分大小写，最终通过模块翻译表和直接导入关系核对。
            keys.update(key.decode("utf-8") for _, key in _TR_KEY.findall(data))
            if re.search(rb"\.\s*tr\s*\(\s*\)", data):
                keys.add(_GENERATED_TR)
    return semantic, frozenset(keys)


# 此特殊标记只让已发现 JSON 键的查询检查调用文件中的生成常量，最终仍需验证导入。
_GENERATED_TR = "<generated-easy-localization-key>"


def _tap_closure(value: list[str]) -> bool:
    """只接受有函数体的零参匿名闭包；null、可空变量和条件回调都不给出点击证据。"""
    if value[:2] != ["(", ")"]:
        return False
    body = value[3:] if value[2:3] == ["async"] else value[2:]
    return body[:1] == ["{"] or body[:2] == ["=", ">"]


def _final_owner(tokens: list[str], equal: int) -> str:
    """在赋值号 equal 检查 final [类型] 变量 = Owner.of(context)[!]; 的完整声明。

    返回生成类候选名称，其他初始化表达式返回空串；稍后还需用当前模块的资源配置确认生成类。
    """
    # 第一种是类型推导，第二种要求显式变量类型与右侧生成类相同。
    if equal < 2 or not tokens[equal - 1].isidentifier():
        return ""
    owner = tokens[equal + 1] if equal + 1 < len(tokens) else ""
    if not owner.isidentifier() or (
        tokens[equal - 2] != "final"
        and not (equal >= 3 and tokens[equal - 3 : equal - 1] == ["final", owner])
    ):
        return ""
    if (
        tokens[equal + 2 : equal + 5] != [".", "of", "("]
        or tokens[equal + 6 : equal + 7] != [")"]
        or not tokens[equal + 5].isidentifier()
    ):
        return ""
    end = equal + 7
    return owner if tokens[end : end + 1] == [";"] or tokens[end : end + 2] == ["!", ";"] else ""


def _text_argument(value: list[str]) -> list[str]:
    """仅展开直接的 Text(首参数)；构造后再调用、多个控件、独立语义名称等不用于关联。"""
    # 可选 const 不改变 Text 的显示内容；只允许整个表达式就是这个构造调用。
    if value[:1] == ["const"]:
        value = value[1:]
    if value[:2] != ["Text", "("] or value[-1:] != [")"]:
        return []
    depth = 1
    for position, token in enumerate(value[2:], start=2):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        if depth == 0 and position != len(value) - 1:
            return []
    if depth:
        return []
    # semanticsLabel 会改变读屏名称，动态或另设语义的 Text 留给其他证据。
    args = list(arguments(value, 2))
    return (
        args[0] if args and not any(arg[:2] == ["semanticsLabel", ":"] for arg in args[1:]) else []
    )


def _text_label(value: list[str], easy_tr: bool) -> list[str]:
    """读取直连 Text 文案；无参数 Text.tr() 的实际资源键由首个参数提供。"""
    if easy_tr and value[-4:] == [".", "tr", "(", ")"]:
        key = _text_argument(value[:-4])
        return [*key, ".", "tr", "(", ")"] if key else []
    return _text_argument(value)


@lru_cache(maxsize=64)
def _declarations(path: Path, modified: int, changed: int, size: int) -> tuple[Declaration, ...]:
    """读取初筛命中的 Dart 文件，验证同一构造调用中的 ID、文案和点击回调。

    路径、mtime/ctime 纳秒及大小字节决定解析缓存；不执行 Dart。另一构造调用中的字段、
    有歧义的标识和动态值均不产生对应证据。解码或读取异常由外层处理。
    """
    tokens = tokenize(_bytes(path, size).decode("utf-8-sig"))
    # 忽略项目内的同名 Semantics 类型，避免误以为它会映射原生 identifier。
    # 缓存该文件直接导入的无前缀库，决定哪些标准 Widget 的字段契约可用。
    libraries = {
        string_literal(tokens[i + 1])
        for i, token in enumerate(tokens[:-2])
        if token == "import" and tokens[i + 2] == ";"
    }
    if not libraries.intersection(_FRAMEWORK_LIBRARIES):
        return ()
    shadowed = {
        tokens[i + 1]
        for i, t in enumerate(tokens[:-1])
        if t in ("class", "typedef", "mixin", "enum")
    }
    # 生成键类可能被同名局部变量覆盖。没有完整 Dart 类型解析时保守拒绝这些标识。
    local_variables = {
        token
        for i, token in enumerate(tokens[1:-1], start=1)
        if token and token[0].isupper() and tokens[i + 1] == "="
    }
    # 每个花括号块只继承当前可证实的不可变绑定；退出即恢复外层名称。
    scopes: list[dict[str, str]] = [{}]
    # 只有导入 Material UI 时才能按 Material 的 child / tooltip 契约关联名称和动作。
    material = bool(
        libraries.intersection(
            {"package:flutter/material.dart", "package:material_ui/material_ui.dart"}
        )
    )
    # CupertinoButton 的 Text 内容可提供文案，但必须看到 Cupertino 的公开导入。
    cupertino = bool(
        libraries.intersection(
            {"package:flutter/cupertino.dart", "package:cupertino_ui/cupertino_ui.dart"}
        )
    )
    # 自定义扩展可能改变 tr() 的解析；没有完整类型分析时保守放弃这种关联。
    easy_tr = EASY_LOCALIZATION_LIBRARY in libraries and "extension" not in tokens
    found: list[Declaration] = []
    for i, widget in enumerate(tokens[:-1]):
        if widget == "{":
            scopes.append(scopes[-1].copy())
        elif widget == "}" and len(scopes) > 1:
            scopes.pop()
        elif widget == "=" and len(scopes) > 1:
            owner = _final_owner(tokens, i)
            if owner and owner not in shadowed:
                scopes[-1][tokens[i - 1]] = owner
        elif widget in scopes[-1] and tokens[i + 1] != ".":
            # 裸变量可能是新声明或回调的同名形参；不跨越无法证明绑定关系的位置。
            scopes[-1].pop(widget)
        # 自定义 Widget 同样可以具有明确的 title 与点击闭包，不对任何业务类名单独适配。
        if not widget[:1].isupper() or i and tokens[i - 1] == ".":
            continue
        # 标准按钮的 .icon 构造器显式区分 label 和旁边的图标。
        material_button = material and widget in _MATERIAL_BUTTONS and widget not in shadowed
        icon_constructor = material_button and tokens[i + 1 : i + 4] == [".", "icon", "("]
        floating = material and widget == "FloatingActionButton" and widget not in shadowed
        # FAB 的公开命名构造与默认构造共享 tooltip 语义。
        floating_named = (
            floating
            and tokens[i + 1 : i + 2] == ["."]
            and i + 3 < len(tokens)
            and tokens[i + 2] in {"small", "large", "extended"}
            and tokens[i + 3] == "("
        )
        named_constructor = icon_constructor or floating_named
        if tokens[i + 1] != "(" and not named_constructor:
            continue
        semantic = widget == "Semantics" and widget not in shadowed
        params = {
            arg[0]: arg[2:]
            for arg in arguments(tokens, i + (4 if named_constructor else 2))
            if len(arg) >= 3
            and arg[1] == ":"
            and arg[0] in ("identifier", "label", "title", "child", "tooltip", "onTap", "onPressed")
        }
        id_tokens = params.get("identifier", [])
        identifier = string_literal(id_tokens[0]) if semantic and len(id_tokens) == 1 else ""
        # action 只说明源码声明了点击闭包。安装包运行时是否有可点节点仍由当前 UI 树确认。
        action = (
            "tap" if any(_tap_closure(params.get(k, [])) for k in ("onTap", "onPressed")) else ""
        )
        if not identifier and not action:
            continue
        # 含 label 和 title 的自定义控件可能显示不同文案，不能替应用选择取哪一个。
        fields = [
            params[k]
            for k in ("label", "title")
            if k in params and (semantic is False or k == "label")
        ]
        label_tokens = fields[0] if len(fields) == 1 else []
        # Flutter SDK 明确把这些字段用作控件标签。仅展开紧邻的 Text，避免借用嵌套按钮的名称。
        runtime_widget = material and widget not in shadowed
        cupertino_button = cupertino and widget == "CupertinoButton" and widget not in shadowed
        if (material_button or cupertino_button) and "Text" not in shadowed:
            label_tokens = _text_label(
                params.get("label" if icon_constructor else "child", []), easy_tr
            )
        elif runtime_widget and widget == "ListTile" and "Text" not in shadowed:
            label_tokens = _text_label(params.get("title", []), easy_tr)
        elif runtime_widget and widget in {"IconButton", "FloatingActionButton"}:
            label_tokens = params.get("tooltip", [])
            if floating and not label_tokens and "Text" not in shadowed:
                # extended 的 label 与默认控件的 child 均可直接使用 Text。
                field = "label" if floating_named and tokens[i + 2] == "extended" else "child"
                label_tokens = _text_label(params.get(field, []), easy_tr)
        owner = key = ""
        label = string_literal(label_tokens[0]) if len(label_tokens) == 1 else ""
        if (
            len(label_tokens) in (8, 9)
            and label_tokens[1:4] == [".", "of", "("]
            and label_tokens[4].isidentifier()
            and label_tokens[5] == ")"
            and (
                label_tokens[6:-1] in (["."], ["!", "."])
                and label_tokens[-1].isidentifier()
                and label_tokens[0] not in shadowed
            )
        ):
            # 所属模块随后还会核对生成类名及 ARB 中的精确键。
            owner, key = label_tokens[0], label_tokens[-1]
        elif (
            len(label_tokens) == 5
            and label_tokens[1:4] == [".", "current", "."]
            and label_tokens[0].isidentifier()
            and label_tokens[0] not in shadowed
            and label_tokens[-1].isidentifier()
        ):
            # 生成类的 current 与业务单例同形，模块配置必须明确提供此访问器才会产生译文。
            owner, key = f"{label_tokens[0]}.current", label_tokens[-1]
        elif easy_tr and len(label_tokens) == 5 and label_tokens[1:] == [".", "tr", "(", ")"]:
            key = string_literal(label_tokens[0])
            if key:
                owner = EASY_LOCALIZATION_LIBRARY
        elif len(label_tokens) == 3 and label_tokens[1] == "." and label_tokens[-1].isidentifier():
            # 仅使用最近可见的 final 本地化对象，普通业务对象的同名 getter 无法提供翻译证据。
            owner = scopes[-1].get(label_tokens[0], "")
            if owner:
                key = label_tokens[-1]
        # 同库无参数 tr() 的静态 Class.member 继续沿导入确认；不把任意 getter 当成资源键。
        key_constant = ""
        if (
            easy_tr
            and len(label_tokens) == 7
            and label_tokens[3:] == [".", "tr", "(", ")"]
            and label_tokens[1] == "."
            and label_tokens[0].isidentifier()
            and label_tokens[0] not in shadowed
            and label_tokens[0] not in local_variables
            and label_tokens[2].isidentifier()
        ):
            owner = EASY_LOCALIZATION_LIBRARY
            key_constant = ".".join((label_tokens[0], label_tokens[2]))
        if identifier or key or label or key_constant:
            found.append(Declaration(identifier, label, owner, key, action, widget, key_constant))
    return tuple(found)


def declarations(
    module: Path,
    requested_keys: set[str],
    requested_labels: set[str] | None = None,
    *,
    has_json_labels: bool = False,
) -> Iterator[tuple[Path, Declaration]]:
    """只枚举 module/lib 下普通 Dart 文件的声明及来源，软链接目录不跟随。

    requested_keys 为已解析的 ARB 原始键，requested_labels 是用于词法初筛的本批查询名称。
    两者都为空时只检查显式语义 ID。has_json_labels 表示当前模块已确认本批的 JSON 翻译，
    此时才检查相应的无参数 tr() 调用和生成键。

    每次用 stat 发现增删改，仅解析签名改变的文件；超出整个模块的数量/字节上限或文件不可读，
    抛出错误供调用方放弃本次 ID 结果，避免部分索引看似唯一。所有文件均在返回前关闭。
    """
    # requested_labels 是当前批次完整名称，可选择声明了直写标签的按钮文件。
    search_terms = requested_keys | {
        " ".join(label.casefold().split()) for label in requested_labels or ()
    }
    if has_json_labels:
        search_terms.add(_GENERATED_TR)
    # lib 自身也可能被软链接替换；不允许跳出已确认的模块目录。
    lib = module / "lib"
    if not lib.is_dir() or lib.is_symlink():
        return
    # 计数包含所有扫描的文件，字节数为此次模块扫描的累计 Dart 源文件大小。
    count = total_bytes = 0
    for directory, subdirs, filenames in os.walk(lib, followlinks=False):
        # 跳过隐藏工程缓存以及目录链接，父路径保持在授权库目录内。
        base = Path(directory)
        subdirs[:] = [
            name for name in subdirs if not name.startswith(".") and not (base / name).is_symlink()
        ]
        for name in filenames:
            if not name.endswith(".dart"):
                continue
            path = base / name
            info = path.stat(follow_symlinks=False)
            if not S_ISREG(info.st_mode):
                continue
            count += 1
            total_bytes += info.st_size
            if (
                count > _MAX_DART_FILES
                or total_bytes > _MAX_DART_BYTES
                or info.st_size > _MAX_FILE_BYTES
            ):
                raise ValueError("Dart source scan exceeds limit")
            # 没有查询键使用点且没有框架 ID 时，复用轻量初筛结果，免于逐次解析所有 Dart 文件。
            semantic, keys = _candidates(path, info.st_mtime_ns, info.st_ctime_ns, info.st_size)
            if not semantic and keys.isdisjoint(search_terms):
                continue
            for declaration in _declarations(
                path, info.st_mtime_ns, info.st_ctime_ns, info.st_size
            ):
                yield path, declaration
