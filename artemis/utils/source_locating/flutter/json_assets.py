"""读取 easy_localization 默认 RootBundleAssetLoader 明确装载的静态 JSON 译文。

```text
绑定的 Flutter 模块 + 查找名称
  → 检查 pubspec 依赖及默认入口的 EasyLocalization(...) 声明
  ├─ 无法静态确认加载器、目录或语言 → 该模块没有可用的 JSON 线索
  └─ 确认默认加载器 → 按语言与地区计算文件名，检查 pubspec 资产声明
       → 读取并展开已选语言文件，用查询名称匹配实际资源键或译文
       ├─ 唯一且各语言都有静态值 → 返回译文，按需交付实际资源键
       └─ 所选文件异常、键冲突或含动态文案 → 不提供对应线索
```

返回映射里的空列表表示普通未命中，缺少结果键表示已选资源无法确认。调用方利用这一区别
决定是否还能采纳其他来源。
"""

import json
from pathlib import Path
import re

import yaml

from artemis.agents.explorer.screen_index import normalize_text
from artemis.utils.source_locating.flutter.dart_syntax import arguments, string_literal, tokenize
from artemis.utils.source_locating.common.types import SourceHint

# 只有文件显式导入此扩展时，静态字符串的无参数 tr() 才可指向该模块已确认的翻译资源。
EASY_LOCALIZATION_LIBRARY = "package:easy_localization/easy_localization.dart"


# 每个配置文件、入口文件或语言资源最多 2 MiB；一组语言文件总计最多 32 MiB。
_FILE_LIMIT = 2 * 1024 * 1024
_TOTAL_LIMIT = 32 * 1024 * 1024
# 仅识别 Locale 的静态 languageCode / countryCode 构造，避免猜测动态语言清单。
_LOCALE = re.compile(
    r"""(?:const)?Locale\((['"])([a-z]{2,3})\1(?:,(['"])([A-Z]{2}|[0-9]{3})\3)?[,]?\)"""
)
# easy_localization 的命名占位、位置占位以及 @: / @.upper: 等链接都需要运行时解析。
_DYNAMIC = re.compile(r"[{}]|@(?:\.\w+)?:|^:/")
# 复数对象的最终文案依赖传入的数字，不能当作独立静态别名。
_PLURALS = {"zero", "one", "two", "few", "many", "other"}


def _read(module: Path, relative: Path) -> bytes:
    """读取模块内一个普通 UTF-8 文件的原始字节；路径越界、符号链接或体积超限即放弃。"""
    path = module / relative
    if path.resolve(strict=True) != path or not path.is_relative_to(module) or not path.is_file():
        raise ValueError("Untrusted localization path")
    with path.open("rb") as source:
        payload = source.read(_FILE_LIMIT + 1)
    if len(payload) > _FILE_LIMIT:
        raise ValueError("Localization file exceeds size limit")
    return payload


def _locale_files(options: dict[str, list[str]]) -> tuple[str, ...]:
    """按 supportedLocales 和 useOnlyLangCode 计算实际打包文件名，不能确认列表时返回空元组。"""
    locales = options.get("supportedLocales", [])
    if locales[:1] == ["const"]:
        locales = locales[1:]
    if locales[:3] == ["<", "Locale", ">"]:
        locales = locales[3:]
    only_language = options.get("useOnlyLangCode", ["false"])
    if locales[:1] != ["["] or locales[-1:] != ["]"] or only_language not in (["true"], ["false"]):
        return ()
    files: dict[str, None] = {}
    # 复用 Dart 参数分割器，仅分开 Locale 构造表达式之间的逗号。
    for expression in arguments(locales, 1):
        if not expression:
            continue
        locale = _LOCALE.fullmatch("".join(expression))
        if not locale or len(files) >= 128:
            return ()
        country = locale[4] if only_language == ["false"] else ""
        files[locale[2] + (f"-{country}" if country else "") + ".json"] = None
    return tuple(files)


def _bootstrap(main: str) -> tuple[Path, tuple[str, ...]] | None:
    """从默认入口的一处 runApp(EasyLocalization(...)) 读取字面资源目录和语言清单。

    仅支持无前缀的库导入、默认加载器和可静态展开的 Locale 列表。其他入口或自定义
    loader 可能加载网络、数据库或合并其他文件，此时不根据磁盘上的 JSON 猜测。
    """
    tokens = tokenize(main)
    # 验证实际导入包和单一根 Widget；字符串和注释中的代码样例不会生成构造证据。
    if not any(
        token == "import"
        and string_literal(tokens[i + 1]) == EASY_LOCALIZATION_LIBRARY
        and tokens[i + 2] == ";"
        for i, token in enumerate(tokens[:-2])
    ):
        return None
    if any(
        tokens[i + 1] == "EasyLocalization" and token in {"class", "typedef", "mixin"}
        for i, token in enumerate(tokens[:-1])
    ):
        return None
    calls = [i for i, token in enumerate(tokens[:-1]) if token == "runApp" and tokens[i + 1] == "("]
    if len(calls) != 1:
        return None
    # 只采纳 main 函数最外层直接调用的 runApp。其他辅助函数、条件分支和另一个入口均可能未执行。
    mains = [
        i
        for i, token in enumerate(tokens[:-2])
        if token == "main" and tokens[i + 1 : i + 3] == ["(", ")"]
    ]
    if len(mains) != 1:
        return None
    body = mains[0] + 3
    if tokens[body : body + 1] == ["async"]:
        body += 1
    before = tokens[body : calls[0]]
    if before[:1] == ["{"]:
        # main 的闭括号一旦出现，后面其他函数中的 runApp 不能冒充当前入口。
        depth = 0
        for token in before:
            depth += (token == "{") - (token == "}")
            if depth == 0:
                return None
        if depth != 1 or before[-1] not in {"{", ";"} or "return" in before:
            return None
    elif before not in (["=", ">"], ["=>"]):
        return None
    # 根参数必须是此构造调用本身；取 .child 或在后面接其他表达式会改变实际根节点。
    roots = [arg for arg in arguments(tokens, calls[0] + 2) if arg]
    if len(roots) != 1:
        return None
    widget = roots[0][1:] if roots[0][:1] == ["const"] else roots[0]
    if widget[:2] != ["EasyLocalization", "("] or widget[-1:] != [")"]:
        return None
    depth = 1
    for token in widget[2:-1]:
        depth += (token == "(") - (token == ")")
        if depth == 0:
            return None
    if depth != 1:
        return None
    options = {arg[0]: arg[2:] for arg in arguments(widget, 2) if len(arg) >= 3 and arg[1] == ":"}
    if {"assetLoader", "extraAssetLoaders"} & options.keys():
        return None
    if options.get("useFallbackTranslations", ["false"]) != ["false"]:
        return None
    path = options.get("path", [])
    folder = string_literal(path[0]) if len(path) == 1 else ""
    files = _locale_files(options)
    if not folder or Path(folder).is_absolute() or ".." in Path(folder).parts or not files:
        return None
    return Path(folder), files


def _flatten(document: dict) -> dict[str, str | None]:
    """返回静态文字按点连接的键；plural、插值和链接的值用 None 标记为不确定。"""
    result: dict[str, str | None] = {}

    def visit(branch: dict, prefix: str) -> None:
        """把当前 JSON 对象在 prefix 下可访问的文字写入 result；不解析可执行表达式。"""
        for name, value in branch.items():
            if not name or (prefix and "." in name):
                continue
            key = prefix + name
            if (
                isinstance(value, dict)
                and "." not in name
                and not (_PLURALS.issuperset(value) and "other" in value)
            ):
                visit(value, key + ".")
                continue
            # 空标签或大段正文没有可复用的控件文案证据。
            result[key] = (
                value
                if (
                    isinstance(value, str)
                    and 0 < len(value.strip()) <= 256
                    and not _DYNAMIC.search(value)
                )
                else None
            )

    # 包的 Translations.get 优先精确根键；根键与嵌套键同名时，以真实运行时优先顺序为准。
    visit({name: value for name, value in document.items() if "." not in name}, "")
    visit({name: value for name, value in document.items() if "." in name}, "")
    return result


def read_easy_localization(
    module: Path,
    targets: tuple[str, ...],
    *,
    manifest: dict | None = None,
    matched_keys: dict[str, str] | None = None,
) -> dict[str, list[SourceHint]]:
    """在 module 内读取默认入口实际选择的翻译，按原始 targets 返回供 UI 树核验的标签线索。

    manifest 可复用调用方已解析的 pubspec.yaml；缺省时本地读取。空列表表示没有匹配，
    缺少对应结果表示资源有冲突或无法完整读取，调用方应放弃该查询的跨来源猜测。
    可传入 matched_keys 接收本批静态命中的原始 JSON 键，用来核对同模块 Dart 调用；
    仅清除此批 targets 的旧映射。整个过程同步、只读，不调用 Flutter 或启动 App。
    """
    result: dict[str, list[SourceHint]] = {target: [] for target in targets}
    # 文件修改后可能出现歧义，调用方不能因此次失败而沿用同一目标上次命中的键。
    if matched_keys is not None:
        for target in targets:
            matched_keys.pop(target, None)
    if not targets:
        return result
    try:
        if not module.is_absolute() or module.resolve(strict=True) != module:
            return {}
        if manifest is None:
            if not (module / "pubspec.yaml").is_file():
                return result
            manifest = yaml.safe_load(_read(module, Path("pubspec.yaml"))) or {}
        if not isinstance(manifest, dict) or "easy_localization" not in (
            manifest.get("dependencies") or {}
        ):
            return result
        if not (module / "lib/main.dart").is_file():
            return result
        config = _bootstrap(_read(module, Path("lib/main.dart")).decode("utf-8-sig"))
        if config is None:
            return result
        directory, filenames = config
        # Flutter 目录声明只包含目录下的直接文件。逐文件声明也可以证明该文件被打包。
        assets = {
            entry
            for entry in (manifest.get("flutter") or {}).get("assets", [])
            if isinstance(entry, str)
        }
        catalogs: list[dict[str, str | None]] = []
        byte_count = 0
        for filename in filenames:
            relative = directory / filename
            if directory.as_posix() + "/" not in assets and relative.as_posix() not in assets:
                return {}
            payload = _read(module, relative)
            byte_count += len(payload)
            if byte_count > _TOTAL_LIMIT:
                return {}
            document = json.loads(payload.decode("utf-8-sig"))
            if not isinstance(document, dict):
                return {}
            catalogs.append(_flatten(document))
        # 每个查询名可能是资源键或任一语言的译文；保留多个键以检测同文案歧义。
        wanted = {normalize_text(target) for target in targets}
        owners: dict[str, set[str]] = {name: set() for name in wanted}
        for catalog in catalogs:
            for key, label in catalog.items():
                for alias in (normalize_text(key), normalize_text(label)):
                    if alias in wanted:
                        owners[alias].add(key)
        # 完整解析目录后才向调用方交付本次资源键，避免部分解码结果参与控件关联。
        confirmed: dict[str, str] = {}
        for target in targets:
            keys = owners[normalize_text(target)]
            if not keys:
                continue
            if len(keys) != 1:
                result.pop(target, None)
                continue
            key = next(iter(keys))
            labels = [catalog.get(key) for catalog in catalogs]
            if any(label is None for label in labels):
                result.pop(target, None)
                continue
            result[target] = [
                SourceHint(
                    labels=tuple(dict.fromkeys(labels)), origin=f"{directory.as_posix()}#{key}"
                )
            ]
            confirmed[target] = key
        if matched_keys is not None:
            matched_keys.update(confirmed)
        return result
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        AttributeError,
        RecursionError,
        yaml.YAMLError,
    ):
        return {}
