"""Android Views 和 Compose 共用的工程目录发现与字符串读取。

```text
绑定目录 → Gradle 模块与资源目录
  ├─ 需要解析布局的引用 → 查直接本地依赖及模块 R 命名空间
  └─ 需要翻译文案 → 收集 values 条目，沿同模块字符串引用找最终译文
```

此处只返回目录、资源键和静态文案。各定位器负责将资源关联到实际控件声明。
"""

from __future__ import annotations

from collections import defaultdict
from itertools import islice
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from pygments.lexers.jvm import GroovyLexer, KotlinLexer
from pygments.token import Comment, Error, String

# values 或布局中的未限定字符串资源；限定包名、主题属性和运行期表达式不在本模块解析。
_STRING_REF = re.compile(r"@string/([A-Za-z_][\w.]*)", re.ASCII)
# 常量包名限定符。不接受版本目录引用、Gradle 属性或字符串插值。
_PACKAGE = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+"
# 仅识别 Gradle settings 中直接声明的静态模块路径，不执行工程提供的脚本。
_INCLUDES = re.compile(r"(?m)^\s*include\s*(?:\(([^)]*)\)|([^\n]+))")
# 只采纳直接声明在 Gradle dependencies 块中的本地依赖。变体专用配置留给当前构建核验。
_LOCAL_DEPENDENCY = re.compile(r"\s*(?:\(\s*)?project\s*\(\s*(['\"])(:[a-zA-Z0-9_:-]+)\1\s*\)")
# Android 菜单 XML 提供菜单项的标题及 ID，布局则提供 View 的可见文字或描述。
_UI_RESOURCE_TYPES = {"layout", "menu"}
# 每次查询限制枚举条目、XML 数量和体积；超限时放弃整个 Android 候选集合。
_MAX_ENTRIES = 4096
_MAX_FILE_BYTES = 2 * 1024 * 1024


def _entries(directory: Path) -> list[Path]:
    """只枚举 directory 下的直接子项；超过上限抛出 ValueError，不能用部分清单推断唯一性。"""
    entries = list(islice(directory.iterdir(), _MAX_ENTRIES + 1))
    if len(entries) > _MAX_ENTRIES:
        raise ValueError("Resource directory exceeds discovery budget")
    return sorted(entries)


def _real_dir(path: Path) -> bool:
    """检查派生自绑定根的 path 是真实目录。遇到符号链接或目录缺失时不继续向下枚举。"""
    return path.is_dir() and path.resolve(strict=True) == path


def _resource_dirs(root: Path) -> list[Path]:
    """枚举 root 中 XML layouts/values 的真实子目录，包含带语言或方向等限定符的目录。"""
    return [
        directory
        for directory in _entries(root)
        if directory.name.split("-", 1)[0] in _UI_RESOURCE_TYPES | {"values"}
        and _real_dir(directory)
    ]


def _workspaces(root: Path) -> tuple[Path, ...]:
    """查找绑定根及框架标准宿主；仓库只有一个下级 Gradle 项目时也接受其任意目录名。"""
    # 标准 Android 宿主仍适用于混合工程。独立示例仓库含多个 app 时，不能猜用户绑定的是哪一个。
    standard = (root, root / "android")
    settings_names = ("settings.gradle.kts", "settings.gradle")
    if any((folder / name).is_file() for folder in standard for name in settings_names):
        return standard
    projects = [
        child
        for child in _entries(root)
        if not child.name.startswith(".")
        and _real_dir(child)
        and any((child / name).is_file() for name in settings_names)
    ]
    return (*standard, projects[0]) if len(projects) == 1 else standard


def _direct_libraries(module: Path, included: dict[str, Path]) -> set[Path]:
    """读取模块对 included 中项目的直接 api/implementation 依赖，只返回真实绑定目录。"""
    # 同一模块有两种标准 Gradle 文件名；只检查实际存在、没有重定向的构建文件。
    for filename, lexer in (("build.gradle.kts", KotlinLexer()), ("build.gradle", GroovyLexer())):
        path = module / filename
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            data = stream.read(_MAX_FILE_BYTES + 1)
        if len(data) > _MAX_FILE_BYTES:
            return set()
        script = data.decode("utf-8-sig")
        if "dependencies" not in script or not re.search(r"\bproject\s*\(", script):
            return set()
        # 字符串和注释可能包含同样的示例代码，只有实际标记能声明依赖。
        blocks: list[str] = []
        previous = ""
        libraries: set[Path] = set()
        for position, kind, word in lexer.get_tokens_unprocessed(script):
            if kind in Comment or kind in String or not word.strip():
                continue
            if word == "{":
                blocks.append(previous)
            elif word == "}" and blocks:
                blocks.pop()
            elif blocks == ["dependencies"] and word in {"api", "implementation"}:
                match = _LOCAL_DEPENDENCY.match(script, position + len(word))
                if match and match[2] in included:
                    libraries.add(included[match[2]])
            previous = word
        return libraries
    return set()


def _modules(
    roots: tuple[Path, ...],
    *,
    include_empty: bool = False,
    libraries: dict[Path, set[Path]] | None = None,
) -> dict[Path, set[Path]]:
    """返回模块对应的资源根。roots 是已授权绝对路径，只读发现标准 sourceSets 及显式绑定的资源目录。

    除了直接绑定的模块，还支持 Gradle settings 中的静态多级模块，以及混合工程中的 Android 宿主。
    自定义 Gradle 代码无法静态还原时，用户可直接绑定相应资源目录。
    include_empty 为真时保留只含源码的模块，供 Compose 核对本地及跨模块资源引用。
    libraries 若给出，用于返回已验证的直接本地依赖关系，后续解析不会跨 Gradle 构建借用文案。
    """
    # 模块之间各自解析 @string 引用，避免把另一个模块同名键误当作当前 View 的文案。
    modules: dict[Path, set[Path]] = defaultdict(set)
    for root in roots:
        if not root.is_absolute() or not _real_dir(root):
            continue
        # 用户也可绑定一个任意名称的资源目录，目录的直接子项才是 layout / values。
        if _resource_dirs(root):
            owner = (
                root.parents[2] if root.name == "res" and root.parent.parent.name == "src" else root
            )
            modules[owner].add(root)
        for workspace in _workspaces(root):
            if not _real_dir(workspace):
                continue
            # 有 Gradle settings 时只接受它列出的模块，未加入构建的同级工程不参与匹配。
            candidates = {workspace}
            # Gradle 项目路径与磁盘路径分离，供模块内的 project() 依赖查找。
            included: dict[str, Path] = {}
            has_settings = False
            for filename in ("settings.gradle", "settings.gradle.kts"):
                settings = workspace / filename
                if not settings.is_file() or settings.is_symlink():
                    continue
                has_settings = True
                # 只解释标准 include(":group:module") 和 Groovy include ':group:module'。
                with settings.open("rb") as stream:
                    text = stream.read(_MAX_FILE_BYTES + 1)
                if len(text) > _MAX_FILE_BYTES:
                    raise ValueError("Gradle settings exceeds discovery budget")
                for call in _INCLUDES.finditer(text.decode("utf-8-sig")):
                    for name in re.findall(
                        r"""["']:?([a-zA-Z0-9_-]+(?::[a-zA-Z0-9_-]+)*)["']""", call[1] or call[2]
                    ):
                        directory = workspace.joinpath(*name.split(":"))
                        if _real_dir(directory):
                            included[f":{name}"] = directory
                            candidates.add(directory)
            if not has_settings:
                # 显式绑定部分源码时有可能只包含模块目录及其 manifest，缺少根工程的 settings。
                candidates.update(
                    p
                    for p in _entries(workspace)
                    if not p.name.startswith(".") and _real_dir(p / "src")
                )
            for module in candidates:
                if not _real_dir(module / "src"):
                    continue
                if not any(
                    (module / marker).is_file()
                    for marker in (
                        "src/main/AndroidManifest.xml",
                        "build.gradle",
                        "build.gradle.kts",
                    )
                ):
                    continue
                if include_empty:
                    modules.setdefault(module, set())
                for source_set in _entries(module / "src"):
                    # 测试 APK 的资源不属于用户当前操作的业务 App。
                    if source_set.name.startswith(("androidTest", "testFixtures", "test")):
                        continue
                    resources = source_set / "res"
                    if _real_dir(resources):
                        modules[module].add(resources)
                if libraries is not None and included and module in modules:
                    libraries.setdefault(module, set()).update(_direct_libraries(module, included))
    return modules


def _plain(value: str) -> bool:
    """判断 value 能否直接比对运行时文案。插值、资源间接引用和待 AAPT 解码的文本交由原定位链。"""
    return bool(
        value and len(value) <= 256 and value[0] not in "@?" and not re.search(r'[%\\{}"]', value)
    )


# 资源键 → 语言限定符 → 该限定符的静态值。空语言代表默认资源，多个值表示冲突。
_Strings = dict[str, dict[str, set[str]]]


def _record_strings(document: ET.Element, directory: Path, strings: _Strings) -> None:
    """将 values 目录的资源写入本次模块的 strings，保留无法可靠解读的值作为冲突标记。"""
    # 识别语言、区域及 BCP 47。方向、明暗和构建变体都不能作为不同语言混合。
    qualifier = re.match(
        r"^values(?:-mcc\d+)?(?:-mnc\d+)?-(b\+[A-Za-z0-9+]+|(?!(?:car)(?:-|$))[a-z]{2,3}(?:-r[A-Z]{2})?)(?:-|$)",
        directory.name,
    )
    locale = qualifier[1] if qualifier else ""
    for item in document:
        if item.tag != "string" and not (item.tag == "item" and item.get("type") == "string"):
            continue
        # XML 实体由 ElementTree 解码，带格式子节点或运行时参数的值留空。
        key = item.get("name", "")
        literal = (item.text or "").strip()
        # 先读取未加引号的完整引用，避免把引号内以 @ 开头的展示文案误解为资源别名。
        if not len(item) and _STRING_REF.fullmatch(literal):
            strings[key][locale].add(literal)
            continue
        # AAPT2 允许用一对双引号包住整段字符串以保留空白。运行时显示其内的文本。
        if len(literal) >= 2 and literal[0] == literal[-1] == '"':
            literal = literal[1:-1]
        # AAPT2 解码反斜杠单引号。其他特殊转义仍沿用下方的保守拒绝规则。
        literal = literal.replace(r"\'", "'")
        strings[key][locale].add(literal if not len(item) and _plain(literal) else "")


def _resolve_string(strings: _Strings, key: str) -> tuple[set[str], set[str]]:
    """沿当前模块的 @string 别名查找最终译文，返回可查询的资源键集合及静态标签集合。

    strings 按资源键和语言收集了当前可见的所有定义，key 是布局所引用的起点。
    别名链各语言声明必须指向同一目标，终点允许按语言提供不同的静态文字。键缺失、
    引用成环、语言变体分歧或某一值需运行时求值时返回两个空集合，不保留半条链。
    """
    # 保存已经访问过的精确键名，同时用于环检测和通过中间资源键定位控件。
    keys: set[str] = set()
    while key not in keys:
        keys.add(key)
        # 相同语言在多个 sourceSet 的值不一致时无法确定安装包使用的定义。
        variants = strings.get(key, {})
        if not variants or any(len(values) != 1 or "" in values for values in variants.values()):
            break
        # 每个语言只有一个确定的文字或一个明确的引用，空标记已在上一步剔除。
        values = {next(iter(options)) for options in variants.values()}
        if all(not value.startswith("@") for value in values):
            return keys, values
        if len(values) != 1:
            break
        # _record_strings 对引用已执行完整的本地命名空间检查。
        key = next(iter(values)).removeprefix("@string/")
    return set(), set()


def _gradle_namespace(source: str, kotlin: bool) -> str | None:
    """只识别顶层 android 配置块直接赋予 namespace 的字符串常量，返回 None 表示未声明。

    source 是单个受大小限制的模块构建文件；kotlin 表示 Kotlin DSL，其余采用 Groovy。
    通过词法器跳过注释和字符串中的代码片段。动态声明、多个所有者或词法错误返回空串。
    """
    # 栈中记录各层代码块前的符号，避免从测试配置、条件分支或另一个插件扩展偷取名称。
    blocks: list[str] = []
    previous = ""
    owners: list[str] = []
    lexer = KotlinLexer() if kotlin else GroovyLexer()
    for offset, kind, word in lexer.get_tokens_unprocessed(source):
        if kind in Error:
            return ""
        if kind in Comment or kind in String or not word.strip():
            continue
        if word == "{":
            blocks.append(previous)
        elif word == "}" and blocks:
            blocks.pop()
        elif word == "namespace":
            assignment = re.match(
                rf"[ \t]*(?:=[ \t]*)?(['\x22])({_PACKAGE})\1[ \t]*;?[ \t]*(?=//|\r?\n|}}|$)",
                source[offset + len(word) :],
                re.ASCII,
            )
            if blocks != ["android"] or not assignment or owners:
                return ""
            owners.append(assignment[2])
        previous = word
    return owners[0] if owners else None


def _local_r_namespace(module: Path, roots: tuple[Path, ...]) -> str:
    """只读 module 内可静态确认的 R 包名；资源目录单独授权时不越过 roots 查找构建文件。

    优先识别模块 Gradle 文件中直接给 namespace 赋值的普通字符串，支持 Kotlin 和 Groovy。
    未声明时兼容旧工程 manifest 的 package；动态、重复、不安全或无法判读的声明返回空串。
    """
    if not any(module.is_relative_to(root) for root in roots):
        return ""
    # 仅接受明确写出的包名，避免把 applicationId、调试后缀或字符串模板当作 R 的命名空间。
    try:
        for filename in ("build.gradle.kts", "build.gradle"):
            path = module / filename
            if not path.is_file():
                continue
            if path.resolve(strict=True) != path:
                return ""
            with path.open("rb") as src:
                data = src.read(_MAX_FILE_BYTES + 1)
            if len(data) > _MAX_FILE_BYTES:
                return ""
            source = data.decode("utf-8-sig")
            if "namespace" in source:
                explicit = _gradle_namespace(source, filename.endswith(".kts"))
                if explicit is not None:
                    return explicit
        # 老式模块的主清单直接声明 R 所属包名。不读绑定根之外的清单或带 DTD 的 XML。
        manifest = module / "src/main/AndroidManifest.xml"
        if manifest.resolve(strict=True) != manifest:
            return ""
        with manifest.open("rb") as src:
            data = src.read(_MAX_FILE_BYTES + 1)
        if len(data) <= _MAX_FILE_BYTES and b"<!DOCTYPE" not in data:
            package = ET.fromstring(data).get("package", "")
            return package if re.fullmatch(_PACKAGE, package, re.ASCII) else ""
    except (OSError, UnicodeError, ET.ParseError, RuntimeError):
        pass
    return ""
