"""Small, conflict-checked edits to the active model file, preserving JSONC comments."""

from dataclasses import dataclass, field
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import get_args

from artemis.config.constants import LLMProvider
from artemis.config.llm import LLMConfig, LLMConfigUtils, _expand_default_into_nodes
from artemis.config.paths import get_config_path
from third_party.mobile_use.utils.file import load_jsonc

FIELDS = {
    "provider",
    "model",
    "reasoning_effort",
    "thinking_level",
    "thinking_budget",
    "temperature",
    "timeout",
    "include_thoughts",
    "enable_grounding",
}
ROLES = tuple(name for name in LLMConfig.model_fields if name != "utils") + tuple(
    LLMConfigUtils.model_fields
)
PROVIDERS = get_args(LLMProvider)
LOCK = threading.Lock()
MAX_BYTES = 2 * 1024 * 1024


class SettingsError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


@dataclass
class Span:
    start: int
    end: int
    members: dict = field(default_factory=dict)


def spans(text: str) -> Span:
    """Locate object values without rewriting comments or unrelated configuration."""
    tokens = list(
        re.finditer(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/|[^\s{}\[\]:,]+|[{}\[\]:,]', text)
    )
    tokens = [t for t in tokens if not t[0].startswith(("//", "/*"))]
    index = 0

    def value():
        nonlocal index
        token = tokens[index]
        index += 1
        node = Span(token.start(), token.end())
        if token[0] == "{":
            while tokens[index][0] != "}":
                key = tokens[index]
                index += 2  # key and colon; the full document is validated separately
                child = value()
                name = json.loads(key[0])
                if name in node.members:
                    raise SettingsError("配置包含重复字段，请先在文件中消除歧义。")
                node.members[name] = (key.start(), child)
                if tokens[index][0] == ",":
                    index += 1
            node.end = tokens[index].end()
            index += 1
        elif token[0] == "[":
            while tokens[index][0] != "]":
                value()
                if tokens[index][0] == ",":
                    index += 1
            node.end = tokens[index].end()
            index += 1
        return node

    try:
        return value()
    except (IndexError, ValueError, TypeError):
        raise SettingsError("无法安全编辑该配置文件，请检查 JSONC 格式。") from None


def active_path() -> Path:
    for name in ("artemis.jsonc", "artemis.json", "llm-config.json"):
        try:
            return get_config_path(name).resolve()
        except FileNotFoundError:
            continue
    raise SettingsError("未找到模型配置文件。", 503)


def read_file():
    path = active_path()
    try:
        data = path.read_bytes()
        if len(data) > MAX_BYTES:
            raise SettingsError("配置文件过大，无法在页面中编辑。")
        text = data.decode("utf-8")
        raw = load_jsonc(io.StringIO(text))
        if (
            not isinstance(raw, dict)
            or not isinstance(raw.get("default"), dict)
            or not isinstance(raw.get("nodes", {}), dict)
            or ("planner" in raw and "utils" in raw)
        ):
            raise SettingsError("可视化编辑需要 default/nodes 配置格式，旧版展开格式请先手动迁移。")
        spans(text)
        LLMConfig.model_validate(_expand_default_into_nodes(raw))
        return path, text, raw, hashlib.sha256(data).hexdigest()
    except SettingsError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        raise SettingsError("模型配置无法解析，请先修复文件；原文件没有被修改。", 503) from None


def public_model(model):
    if not isinstance(model, dict):
        return {}
    result = {k: v for k, v in model.items() if k in FIELDS}
    if isinstance(model.get("fallback"), dict):
        result["fallback"] = public_model(model["fallback"])
    return result


def snapshot():
    path, _, raw, version = read_file()
    from artemis.config import settings

    return {
        "version": version,
        "filename": path.name,
        "default": public_model(raw["default"]),
        "nodes": {k: public_model(v) for k, v in raw.get("nodes", {}).items() if k in ROLES},
        "roles": list(ROLES),
        "providers": list(PROVIDERS),
        "credentials": {
            p: bool(settings.get_api_key(p))
            for p in ("google", "openai", "anthropic", "openrouter", "xai", "ocr")
        },
    }


def validate_edit(path, value, remove):
    parts = path.split(".")
    if parts[0] == "default":
        tail = parts[1:]
    elif len(parts) >= 3 and parts[0] == "nodes" and parts[1] in ROLES:
        tail = parts[2:]
    else:
        raise SettingsError("不支持修改该配置字段。")
    fallback = len(tail) == 2 and tail[0] == "fallback"
    if not ((len(tail) == 1 or fallback) and tail[-1] in FIELDS):
        raise SettingsError("不支持修改该模型字段。")
    name = tail[-1]
    if fallback and name == "timeout":
        raise SettingsError("备用模型不支持独立超时。")
    if remove:
        return parts
    if name == "provider":
        valid = value in PROVIDERS if isinstance(value, str) else False
    elif name == "model":
        valid = (
            isinstance(value, str)
            and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}", value))
            and "://" not in value
        )
    elif name == "reasoning_effort":
        valid = value in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
    elif name == "thinking_level":
        valid = value in ("minimal", "low", "medium", "high")
    elif name in ("include_thoughts", "enable_grounding"):
        valid = isinstance(value, bool)
    else:
        valid = (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
        if valid:
            valid = (
                0 <= value <= 2
                if name == "temperature"
                else 1 <= value <= 3600
                if name == "timeout"
                else isinstance(value, int) and -1 <= value <= 1000000
            )
    if not valid:
        raise SettingsError("模型字段值无效，请检查提供商、模型名称和参数范围。")
    return parts


def edit_text(text, parts, value, remove=False):
    root = spans(text)
    parent = root
    for depth, name in enumerate(parts[:-1]):
        if name not in parent.members:
            if remove:
                return text
            text = edit_text(text, parts[: depth + 1], {})
            return edit_text(text, parts, value, remove)
        parent = parent.members[name][1]
        if text[parent.start] != "{":
            raise SettingsError("配置结构不是对象，请先检查该角色配置。")
    name = parts[-1]
    if name in parent.members:
        start, node = parent.members[name]
        if not remove:
            return text[: node.start] + json.dumps(value, ensure_ascii=False) + text[node.end :]
        entries = list(parent.members.items())
        position = [k for k, _ in entries].index(name)
        end = node.end
        if position < len(entries) - 1:
            comma = delimiter(text, end, entries[position + 1][1][0])
            return text[:start] + text[end:comma] + text[comma + 1 :]
        elif position:
            previous = entries[position - 1][1][1]
            comma = delimiter(text, previous.end, start)
            return text[:comma] + text[comma + 1 : start] + text[end:]
        return text[:start] + text[end:]
    if remove:
        return text
    # Prefix the comma before trailing comments, not after a // comment.
    insertion = parent.start + 1
    prefix = ""
    if parent.members:
        insertion = next(reversed(parent.members.values()))[1].end
        prefix = ","
    indent = " " * (len(parts) * 2)
    fragment = (
        prefix + "\n" + indent + json.dumps(name) + ": " + json.dumps(value, ensure_ascii=False)
    )
    return text[:insertion] + fragment + text[insertion:]


def delimiter(text, start, end):
    for token in re.finditer(r"//[^\n]*|/\*[\s\S]*?\*/|,", text[start:end]):
        if token[0] == ",":
            return start + token.start()
    raise SettingsError("无法安全移除字段，请检查配置格式。")


def save(version, edits):
    with LOCK:
        path, text, _, current = read_file()
        if version != current:
            raise SettingsError("配置文件已被修改，请重新加载后再保存；未覆盖外部改动。", 409)
        updated = text
        for edit in edits:
            parts = validate_edit(edit.path, edit.value, edit.remove)
            updated = edit_text(updated, parts, edit.value, edit.remove)
        try:
            candidate = load_jsonc(io.StringIO(updated))
            LLMConfig.model_validate(_expand_default_into_nodes(candidate))
        except (ValueError, TypeError, AttributeError, RecursionError):
            raise SettingsError("修改后模型配置不完整或无效；原文件没有被修改。") from None
        if updated != text:
            if path.read_bytes() != text.encode("utf-8"):
                raise SettingsError("配置文件已被修改，请重新加载。", 409)
            from artemis.resources import get_bundled_config_path
            from artemis.config.paths import get_app_dir

            bundled = get_bundled_config_path("artemis.jsonc")
            target = path
            if bundled and path == bundled.resolve():
                target = get_app_dir() / "artemis.jsonc"
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise SettingsError("可写配置已存在，请重新加载后再保存。", 409)
            fd, temporary = tempfile.mkstemp(prefix=".artemis-model-", dir=target.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(updated)
                    handle.flush()
                    os.fsync(handle.fileno())
                if path.read_bytes() != text.encode("utf-8"):
                    raise SettingsError("配置文件已被修改，请重新加载。", 409)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return snapshot()


def persist_key(provider, key):
    """Persist before runtime activation; never report a swallowed dotenv failure as success."""
    from dotenv import set_key
    from artemis.config import settings
    from artemis.config.paths import get_env_file

    names = {
        "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GCP_API_KEY"),
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "openrouter": ("OPEN_ROUTER_API_KEY",),
        "xai": ("XAI_API_KEY",),
        "ocr": ("OCR_API_KEY", "VISION_API_KEY"),
    }[provider]
    with LOCK:
        path = get_env_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_bytes() if path.exists() else None
        if before and len(before) > MAX_BYTES:
            raise SettingsError("环境配置文件过大，无法安全更新。")
        fd, temporary = tempfile.mkstemp(prefix=".artemis-credential-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(before or b"")
            for name in names:
                set_key(temporary, name, key, quote_mode="always")
            if (path.read_bytes() if path.exists() else None) != before:
                raise SettingsError("环境配置已被修改，请重试。", 409)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
            settings.set_api_key(provider, key, persist_to_env=False)
        finally:
            Path(temporary).unlink(missing_ok=True)
