"""按 Flutter / Flutter Intl 的工程配置读取静态文案，再关联模块内的 Semantics 声明。

调用入口是 read_flutter_many。输入为已绑定的绝对目录与同批查询名称，返回按原查询
分组的 SourceHint。每条线索只描述可能出现在设备上的标签或 ID，定位器负责核验当前 UI 树。

```text
源码组的绑定目录 + 本批查找名称
  → 从 pubspec / Dart 包配置发现绑定范围内的模块
  → 按模块配置读取 ARB，按实际初始化配置补查 easy_localization JSON
  ├─ 翻译或资源键冲突 → 丢弃对应查询，继续核对本批其他名称
  └─ 其余查询 → 查 Dart 控件的原始 ID、直接文案和已知翻译键
     ├─ 找到明确的同节点声明 → 返回标签、ID 或点击回调证据
     ├─ 只找到词条 → 返回可确认的译文供后续真机比对
     └─ 缺少源码证据 → 返回空列表
```

配置、ARB 与 Dart 声明以当前文件签名检查更新。定制路径来自项目配置或显式绑定。
"""

from dataclasses import dataclass, field
from functools import lru_cache
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import url2pathname

import yaml

from artemis.agents.explorer.screen_index import normalize_text

from artemis.utils.source_locating.flutter.json_assets import (
    EASY_LOCALIZATION_LIBRARY,
    read_easy_localization,
)
from artemis.utils.source_locating.flutter.generated_keys import imported_keys
from artemis.utils.source_locating.flutter.semantics import declarations
from artemis.utils.source_locating.common.types import SourceHint

# 单份资源或包配置最多接受 2 MiB，整组最多 128 个 ARB、合计 32 MiB。
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_ARB_FILES = 128
_MAX_ARB_BYTES = 32 * 1024 * 1024
# 限定单组源码中要解析配置的本地 Dart 模块数量，包含显式根目录。
_MAX_MODULES = 256
# 文件签名依次为实际路径、mtime/ctime 纳秒和文件大小字节，用于及时淘汰旧翻译。
_File = tuple[Path, int, int, int]


def _inside(path: Path, root: Path) -> Path | None:
    """在同一授权根目录内解析路径，缺失或跳出根目录时返回 None。"""
    try:
        # 比较解析后的实际路径，防止通过符号链接读到另一组或其他本机文件。
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if resolved.is_relative_to(root) else None


def _signature(path: Path) -> _File:
    """取已通过根目录检查的文件签名；超过单文件上限时抛出 ValueError。"""
    info = path.stat()
    if info.st_size > _MAX_FILE_BYTES or not path.is_file():
        raise ValueError("Flutter resource is too large or not a file")
    return path, info.st_mtime_ns, info.st_ctime_ns, info.st_size


def _read(stamp: _File) -> str:
    """按签名读取 UTF-8 资源，字节数与 stat 不符时放弃本轮匹配。"""
    with stamp[0].open("rb") as stream:
        # 防止预检后文件增长导致不受限读取，兼容带 BOM 的 ARB 导出。
        payload = stream.read(_MAX_FILE_BYTES + 1)
    if len(payload) != stamp[3]:
        raise ValueError("Flutter resource changed during lookup")
    return payload.decode("utf-8-sig")


@lru_cache(maxsize=64)
def _config(stamp: _File) -> dict:
    """按签名读取 Flutter YAML 或 Dart 包配置 JSON；只接受对象，不执行配置。"""
    source = _read(stamp)
    config = json.loads(source) if stamp[0].suffix == ".json" else yaml.safe_load(source)
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise ValueError("Invalid Flutter project configuration")
    return config


def _dart_package_dirs(project: Path) -> set[Path]:
    """按 Dart 包配置 v2 解析主工程内的本地模块。缺少配置时只用手动目录。"""
    # rootUri 的相对路径以 package_config.json 的位置为基准。
    config_path = project / ".dart_tool/package_config.json"
    safe_config = _inside(config_path, project)
    if safe_config is None or not safe_config.is_file():
        return set()
    config = _config(_signature(safe_config))
    packages = config.get("packages")
    if config.get("configVersion") != 2 or not isinstance(packages, list):
        return set()

    # 保留真实位于主工程内的模块，其他 checkout 和 Pub 缓存不在自动发现范围内。
    directories: set[Path] = set()
    for package in packages:
        root_uri = package.get("rootUri") if isinstance(package, dict) else None
        if not isinstance(root_uri, str) or not root_uri:
            continue
        uri = urlsplit(urljoin(config_path.as_uri(), root_uri))
        if uri.scheme != "file" or uri.netloc or uri.query or uri.fragment:
            continue
        candidate = Path(url2pathname(uri.path))
        if not candidate.is_relative_to(project):
            continue
        directory = _inside(candidate, project)
        if directory is not None and directory.is_dir():
            directories.add(directory)
    return directories


def _modules(roots: tuple[Path, ...]) -> set[Path]:
    """从绑定 roots、包配置 v2 和 pubspec.workspace 的显式相对目录取得模块。

    新 checkout 可以没有 .dart_tool，静态 workspace 成员可以继续声明嵌套子模块。
    通配 workspace 的成员可由已有包配置或手动绑定指出；这里不递归猜测未知成员。
    """
    modules = set(roots)
    for root in roots:
        modules.update(_dart_package_dirs(root))
    # 已登记路径同时作为去重标记，避免重复成员或指回自身时无限遍历。
    pending = list(modules)
    while pending:
        if len(modules) > _MAX_MODULES:
            raise ValueError("Too many Flutter source modules")
        module = pending.pop()
        manifest = _inside(module / "pubspec.yaml", module)
        if manifest is None:
            continue
        members = _config(_signature(manifest)).get("workspace", [])
        if not isinstance(members, list):
            continue
        for relative in members:
            if (
                not isinstance(relative, str)
                or Path(relative).is_absolute()
                or any(c in relative for c in "*?[")
            ):
                continue
            # 只沿受信根内的、实际存在且有 pubspec 的成员继续遍历。
            child = _inside(module / relative, module)
            if (
                child is not None
                and child not in modules
                and _inside(child / "pubspec.yaml", child)
            ):
                modules.add(child)
                pending.append(child)
    return modules


def _generation(module: Path, roots: tuple[Path, ...]) -> dict[Path, set[str]]:
    """返回 module 指定的 ARB 目录及生成类；跨模块路径也必须落在某个绑定 roots 内。"""
    directories: dict[Path, set[str]] = {}
    # 官方 gen-l10n 的 arb-dir 与 output-class 均以当前模块配置为准。
    l10n_path = _inside(module / "l10n.yaml", module)
    standard = _config(_signature(l10n_path)) if l10n_path is not None else {}
    # Flutter Intl 插件可在 pubspec 中单独声明目录和生成类，不推测项目自定义路径。
    spec_path = _inside(module / "pubspec.yaml", module)
    spec = _config(_signature(spec_path)) if spec_path is not None else {}
    intl = spec.get("flutter_intl")
    if "arb-dir" not in standard and not (isinstance(intl, dict) and "arb_dir" in intl):
        # 用户可直接绑定资源目录。模块已有明确生成目录时，不把根目录下的样例 ARB 混入构建资源。
        directories[module] = set()
    sources = []
    if l10n_path is not None or not isinstance(intl, dict) or intl.get("enabled") is False:
        sources.append(
            (
                standard.get("arb-dir", "lib/l10n"),
                standard.get("output-class", "AppLocalizations"),
                False,
            )
        )
    if isinstance(intl, dict) and intl.get("enabled") is not False:
        sources.append((intl.get("arb_dir", "lib/l10n"), intl.get("class_name", "S"), True))
    for relative, owner, flutter_intl in sources:
        if not isinstance(relative, str) or not relative.strip() or not isinstance(owner, str):
            raise ValueError("Invalid Flutter resource directory or class")
        for root in roots:
            directory = _inside(module / relative, root)
            if directory is not None and directory.is_dir():
                accessors = directories.setdefault(directory, set())
                accessors.add(owner)
                # intl_utils 同时生成静态 current 访问器。只有声明了该生成器的模块才能解引用。
                if flutter_intl:
                    accessors.add(f"{owner}.current")
                break
    return directories


@dataclass
class _Message:
    """同一资源目录的一个 ARB 键；分语言记录静态值，冲突使该键不再提供线索。"""

    # 原始键名用于严格解引用 Dart 生成 getter，不折叠大小写。
    key: str
    # 每种语言最多一个静态值，用于识别同键同语言的冲突。
    translations: dict[str, str] = field(default_factory=dict)
    # 同一目录有重复语言且值冲突时设为 True。
    conflict: bool = False


@lru_cache(maxsize=8)
def _bundle(files: tuple[_File, ...]) -> tuple[dict[str, _Message], dict[str, set[str]]]:
    """解析一个目录中当前版本的 ARB，返回键及精确别名索引；只缓存最多八个资源集合。"""
    messages: dict[str, _Message] = {}
    # 键或某一翻译都可作为查询名称；命中多个键时交由调用方拒绝。
    aliases: dict[str, set[str]] = {}
    for stamp in files:
        path = stamp[0]
        resource = json.loads(_read(stamp))
        if not isinstance(resource, dict):
            raise ValueError("ARB must be a JSON object")
        # 优先使用 ARB 的语言字段，常规 app_en / intl_zh_CN 文件可由名称补足。
        locale = normalize_text(
            resource.get("@@locale") or path.stem.partition("_")[2] or path.stem
        ).replace("-", "_")
        for key, value in resource.items():
            # ARB 元数据与 ICU 动态文案无法从静态资源确认最终显示文本。
            if key.startswith("@") or not isinstance(value, str) or len(value) > 256:
                continue
            if "{" in value or "}" in value or not (label := normalize_text(value)):
                continue
            message = messages.setdefault(key, _Message(key))
            # 同一键的同一语言如果有不同静态值，本处记录冲突而不猜应用构建选了哪一份。
            if normalize_text(message.translations.setdefault(locale, value)) != label:
                message.conflict = True
            for alias in (label, normalize_text(key)):
                aliases.setdefault(alias, set()).add(key)
    return messages, aliases


# 每个 ARB 目录对应一个独立翻译包。aliases 的键为规范化文案或资源键，值为所有匹配键名。
_Bundle = tuple[dict[str, _Message], dict[str, set[str]]]
# 模块路径 → 该模块声明的生成类 → 此类实际使用的本地化键。
_Classes = dict[Path, dict[str, dict[str, _Message]]]


def _sources(roots: tuple[Path, ...]) -> tuple[dict[Path, _Bundle], _Classes]:
    """在本批读取一次各目录的翻译包和生成类映射；roots 为规范化绝对授权目录。

    单文件、文件数、总量或根目录校验失败时抛异常，调用方整批回退。无写入和设备访问。
    """
    if any(not p.is_absolute() or p.resolve(strict=True) != p or not p.is_dir() for p in roots):
        raise ValueError("Invalid Flutter source root")
    # 显式绑定根和同工程内的本地 Dart 模块，按物理路径去重。
    modules = _modules(roots)
    owners: _Classes = {}
    records: dict[Path, _Bundle] = {}
    # 只对首次遇到的真实目录累计该组 ARB 数量及总字节。
    total_files = total_bytes = 0
    for module in sorted(modules):
        owners[module] = {}
        for directory, classes in _generation(module, roots).items():
            if directory not in records:
                stamps = []
                for path in directory.glob("*.arb"):
                    # 文件链接的外部目标不属于该模块或资源目录。
                    if path.is_symlink():
                        continue
                    stamp = _signature(path)
                    total_files += 1
                    total_bytes += stamp[3]
                    if total_files > _MAX_ARB_FILES or total_bytes > _MAX_ARB_BYTES:
                        raise ValueError("Flutter localization scan exceeds limit")
                    stamps.append(stamp)
                records[directory] = _bundle(tuple(sorted(stamps)))
            # 同模块下 getter 的 owner 只使用声明该类的资源目录。
            for owner in classes:
                if owner in owners[module] and owners[module][owner] is not records[directory][0]:
                    raise ValueError("Conflicting Flutter localization class")
                owners[module][owner] = records[directory][0]
    return records, owners


def _arb_hint(wanted: str, bundles: dict[Path, _Bundle]) -> SourceHint | None:
    """用规范化名称 wanted 在所有翻译包查唯一键及其译文，来源冲突抛 ValueError。"""
    matched: _Message | None = None
    origin = ""
    for directory, (messages, aliases) in bundles.items():
        for key in aliases.get(wanted, ()):
            record = messages[key]
            if record.conflict or matched is not None and matched != record:
                raise ValueError("Ambiguous Flutter localization source")
            matched = record
            origin = f"{directory.name}/{key}"
    if matched is None:
        return None
    return SourceHint(labels=tuple(dict.fromkeys(matched.translations.values())), origin=origin)


def _semantic_hints(
    owners: _Classes,
    targets: set[str],
    resource_keys: set[str],
    json_labels: dict[Path, dict[str, tuple[str, ...]]],
) -> dict[str, list[SourceHint]]:
    """一次枚举被绑定模块中的 Semantics 声明，按本批查询原文返回匹配的标签及原始 ID。

    owners 给出各模块 ARB 生成类与词条的关系，json_labels 包含各模块已经核实的
    JSON 键，resource_keys 用于缩小待解析文件。targets 保留用户查询原文，标签可
    归一化匹配，Semantics.identifier 则按原始值比较。解析失败时只丢弃此层证据。
    """
    results: dict[str, list[SourceHint]] = {target: [] for target in targets}
    # 文案允许大小写及空白规范化，identifier 单独和原始 target 作精确匹配。
    wanted = {target: normalize_text(target) for target in targets}
    for module, classes in owners.items():
        if not (module / "pubspec.yaml").is_file() and not (module / "l10n.yaml").is_file():
            continue
        # 完整枚举才使用结果；读到一半失败时只保留独立完成的静态 ARB 线索。
        try:
            # 即使模块没有 ARB，显式写在按钮或 tooltip 上的文字也可以查到。
            catalog = json_labels.get(module, {})
            declared = list(
                declarations(module, resource_keys, targets, has_json_labels=bool(catalog))
            )
        except (OSError, RuntimeError, ValueError):
            return {target: [] for target in targets}
        # 同一个 Dart 文件的生成键导入只查一次，缓存仅在当前批、当前模块内有效。
        key_imports: dict[Path, dict[str, str]] = {}
        for path, declaration in declared:
            # 普通 getter 自带真实键；生成常量的字段名可能被转义或重命名，需要解引用。
            resource_key = declaration.resource_key
            if catalog and declaration.key_constant:
                if path not in key_imports:
                    spec = _inside(module / "pubspec.yaml", module)
                    package_name = _config(_signature(spec)).get("name", "") if spec else ""
                    key_imports[path] = imported_keys(
                        module, path, package_name if isinstance(package_name, str) else ""
                    )
                resource_key = key_imports[path].get(declaration.key_constant, "")
            # 以声明所在模块及实际调用的库区分同名键；普通变量、ValueKey 不提供资源依据。
            if declaration.owner == EASY_LOCALIZATION_LIBRARY:
                labels = catalog.get(resource_key, ())
            else:
                record = classes.get(declaration.owner, {}).get(resource_key)
                if record and record.conflict:
                    continue
                labels = (
                    (declaration.label,)
                    if declaration.label
                    else (tuple(dict.fromkeys(record.translations.values())) if record else ())
                )
            aliases = {normalize_text(label) for label in labels}
            if labels and resource_key:
                aliases.add(normalize_text(resource_key))
            for target, normalized in wanted.items():
                if declaration.identifier == target or normalized in aliases:
                    results[target].append(
                        SourceHint(
                            labels=labels,
                            resource_id=declaration.identifier,
                            origin=f"{path.relative_to(module)}:{declaration.widget}",
                            action=declaration.action,
                        )
                    )
    return results


def _include_json_assets(
    modules: _Classes, result: dict[str, list[SourceHint]], eligible: set[str]
) -> dict[Path, dict[str, tuple[str, ...]]]:
    """将使用 easy_localization 的模块文案并入本批 result，全部读盘限定在绑定模块内。

    eligible 包含 ARB 查询后仍无冲突的名称。若两种资源对同一查询给出不同译文，或
    已选 JSON 无法可靠读取，就清除该目标并停止采纳它的其他线索。返回本批次各模块
    已确认的 JSON 键与译文，供引用该键的 Dart 控件在同一模块内关联。
    """
    by_module: dict[Path, dict[str, tuple[str, ...]]] = {}
    for module in modules:
        # 复用 ARB 发现阶段的 pubspec 解析结果；没有声明该包时无需扫描 Dart 入口或 JSON。
        manifest_path = _inside(module / "pubspec.yaml", module)
        if not eligible or manifest_path is None:
            continue
        manifest = _config(_signature(manifest_path))
        dependencies = manifest.get("dependencies")
        if not isinstance(dependencies, dict) or "easy_localization" not in dependencies:
            continue
        # 只采纳由实际加载器确定位置的词典。缺失键代表解析歧义，空列表代表正常未命中。
        # keys 由读取器记录真正匹配的资源键，不能根据翻译或日志中的文件名反推。
        keys: dict[str, str] = {}
        assets = read_easy_localization(
            module, tuple(eligible), manifest=manifest, matched_keys=keys
        )
        for target in tuple(eligible):
            found = assets.get(target)
            if found == []:
                continue
            if found is None or (
                result[target] and set(result[target][0].labels) != set(found[0].labels)
            ):
                result[target] = []
                eligible.remove(target)
            else:
                by_module.setdefault(module, {})[keys[target]] = found[0].labels
                if not result[target]:
                    result[target] = found
    return by_module


def read_flutter_many(
    roots: tuple[Path, ...], targets: tuple[str, ...]
) -> dict[str, list[SourceHint]]:
    """同步为同帧多个控件查询 Flutter 源码；roots 为被授权绝对目录，targets 保留原文。

    返回原名称到静态线索的映射；没有依据或多目录翻译冲突的名称对应空列表。相同目录的
    配置、翻译和 Dart 声明每批只枚举一次。只按元数据访问绑定组，不写源码或连接设备。
    """
    # 调用方可一次传入「原句 / 已提取名称」或多个控件，空查询和重复项无需重复扫描。
    result: dict[str, list[SourceHint]] = {target: [] for target in targets}
    eligible = {target for target in result if normalize_text(target)}
    if not roots or not eligible:
        return result
    try:
        bundles, owners = _sources(roots)
        # 静态翻译冲突只影响那个名称，对同批的其他控件继续核对。
        resource_keys: set[str] = set()
        for target in tuple(eligible):
            try:
                hint = _arb_hint(normalize_text(target), bundles)
            except ValueError:
                eligible.remove(target)
                continue
            if hint is not None:
                result[target] = [hint]
                # 只对可能引用当前文案键的 Dart 点击控件做完整词法核对。
                for _, aliases in bundles.values():
                    resource_keys.update(aliases.get(normalize_text(target), ()))
        json_labels = _include_json_assets(owners, result, eligible)
        for catalog in json_labels.values():
            resource_keys.update(catalog)
        for target, hints in _semantic_hints(owners, eligible, resource_keys, json_labels).items():
            if hints:
                # 同控件的 ID 和文案可一并核验；有 ID 证据时不另行返回更弱的纯文案命中。
                result[target] = hints
    except (OSError, ValueError, RuntimeError, yaml.YAMLError):
        return {target: [] for target in result}
    return result


def read_flutter(roots: tuple[Path, ...], target: str) -> list[SourceHint]:
    """对单个原始 target 复用批量接口，返回待当前真机树核验的静态候选。"""
    return read_flutter_many(roots, (target,))[target]
