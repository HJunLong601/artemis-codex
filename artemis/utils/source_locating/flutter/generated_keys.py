"""核对 easy_localization 生成的静态键，兼容工程自行选择的类名和导入位置。

```text
Dart 控件使用 Catalog.someField.tr()
  → 找该文件明确声明的无前缀 import
  → 将相对 URI 或同包 URI 解析为当前模块内的真实文件
  ├─ 生成文件包含完整静态字段 → 对照字段值，返回 Catalog.someField 对应的 JSON 键
  └─ 导入越界、无法读取或同一符号得到多个键 → 不提供对应关系
```

调用方用返回的 JSON 键读取本批已确认的语言资源，随后用设备层级确认控件。
"""

from functools import lru_cache
import os
from pathlib import Path
from urllib.parse import unquote

from artemis.utils.source_locating.flutter.dart_syntax import string_literal, tokenize

# 每个 Dart 文件最多读取 2 MiB。超过上限或文件在读取期间被编辑时，跳过该次键关联。
_LIMIT = 2 * 1024 * 1024
# 此标识来自 easy_localization 生成器，避免把普通业务常量当作翻译表。
_GENERATED = "This is code generated via package:easy_localization/generate.dart"


@lru_cache(maxsize=64)
def _source(path: Path, mtime: int, ctime: int, size: int) -> str:
    """按路径、mtime/ctime 纳秒及字节数读取 UTF-8 Dart 源文件，缓存最多 64 个文件。"""
    with path.open("rb") as stream:
        data = stream.read(_LIMIT + 1)
    if len(data) != size:
        raise ValueError("Dart file changed during localization key lookup")
    return data.decode("utf-8-sig")


def _read_local(module: Path, path: Path) -> str:
    """只读取当前模块的常规文件。链接、缺失及超预算文件交给调用方跳过。"""
    if not path.is_relative_to(module) or path.resolve(strict=True) != path or not path.is_file():
        raise ValueError("Untrusted Dart import path")
    stat = path.stat()
    if stat.st_size > _LIMIT:
        raise ValueError("Dart import exceeds size limit")
    return _source(path, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)


def _generated_keys(source: str) -> dict[str, str]:
    """读取完整生成类中的 static const [String] 字段，返回 Class.member 到实际翻译键的映射。"""
    if _GENERATED not in source[:256]:
        return {}
    # 当前生成器只输出一个抽象类，字段是可见的字面常量。遇到其它代码形状则不推断。
    tokens = tokenize(source)
    if tokens[:2] != ["abstract", "class"] or tokens[3:4] != ["{"] or tokens[-1:] != ["}"]:
        return {}
    owner = tokens[2]
    keys: dict[str, str] = {}
    index = 4
    while index < len(tokens) - 1:
        if tokens[index : index + 2] != ["static", "const"]:
            return {}
        index += 2
        if tokens[index : index + 1] == ["String"]:
            index += 1
        declaration = tokens[index : index + 4]
        if len(declaration) != 4:
            return {}
        member, assignment, literal, end = declaration
        key = string_literal(literal)
        qualified = f"{owner}.{member}"
        if (
            assignment != "="
            or end != ";"
            or not member.isidentifier()
            or not key
            or qualified in keys
        ):
            return {}
        keys[qualified] = key
        index += 4
    return keys


def imported_keys(module: Path, use_site: Path, package_name: str) -> dict[str, str]:
    """从 use_site 的无前缀导入追溯同模块生成键；重复声明同一符号时只保留无歧义项。

    package_name 是 pubspec 中模块自己的名字，用于 package URI。空字符串仅接受相对 URI。
    该函数同步读盘，不运行 Dart。单个导入不可读时继续核对其它可读导入。
    """
    try:
        tokens = tokenize(_read_local(module, use_site))
    except (OSError, UnicodeError, RuntimeError, ValueError):
        return {}
    # 映射保存生成字段到 JSON 键，冲突独立记录，发现冲突的符号不会随遍历顺序变化。
    keys: dict[str, str] = {}
    conflicts: set[str] = set()
    prefix = f"package:{package_name}/" if package_name else ""
    for index, token in enumerate(tokens[:-2]):
        if token != "import" or tokens[index + 2] != ";":
            continue
        uri = unquote(string_literal(tokens[index + 1]))
        if not uri or ":" in uri and not (prefix and uri.startswith(prefix)):
            continue
        # Dart 相对 import 可以跨层目录，但最后解析出的路径必须仍位于同一授权模块内。
        imported = (
            module / "lib" / uri.removeprefix(prefix)
            if prefix and uri.startswith(prefix)
            else use_site.parent / uri
        )
        try:
            # 只消解相对目录层级，保留可检测的符号链接组件供受控读取拒绝。
            canonical = Path(os.path.normpath(imported))
            declarations = _generated_keys(_read_local(module, canonical))
        except (OSError, UnicodeError, RuntimeError, ValueError):
            continue
        for symbol, key in declarations.items():
            if symbol in keys and keys[symbol] != key:
                conflicts.add(symbol)
            keys[symbol] = key
    return {symbol: key for symbol, key in keys.items() if symbol not in conflicts}
