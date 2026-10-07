"""Bounded Codex catalog checks; live inference is explicit, never a polling side effect."""

from __future__ import annotations

import asyncio
import copy
import os
from pathlib import Path
import time
from typing import Any

from artemis.config.llm import LLMConfig
from artemis.llm.codex_app_server import CodexAppServerClient, find_codex_binary
from artemis.llm.reliability import classify_failure

_CATALOG_TTL = 60.0
_catalog_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}


class ModelPreflightError(RuntimeError):
    """Model configuration cannot be used by the current client."""


async def _catalog(client: CodexAppServerClient, *, force: bool) -> list[dict[str, Any]]:
    key = (client.binary, os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    cached = _catalog_cache.get(key)
    if not force and cached and time.monotonic() - cached[0] < _CATALOG_TTL:
        return copy.deepcopy(cached[1])
    models: list[dict[str, Any]] = []
    cursor = None
    seen = set()
    for _ in range(20):
        params: dict[str, Any] = {"limit": 100, "includeHidden": True}
        if cursor:
            params["cursor"] = cursor
        page = await client.request("model/list", params)
        models.extend(page.get("data") or [])
        cursor = page.get("nextCursor")
        if not cursor:
            if len(_catalog_cache) >= 16:
                _catalog_cache.clear()
            _catalog_cache[key] = (time.monotonic(), models)
            return copy.deepcopy(models)
        if cursor in seen:
            break
        seen.add(cursor)
    raise ModelPreflightError("Codex model catalog pagination did not finish.")


async def preflight_models(
    config: LLMConfig, *, verify_inference: bool = False, force: bool = False
) -> dict[str, Any]:
    """Check unique Codex model/effort pairs; unused fallbacks are advisory.

    Catalog presence is not an entitlement check. Inference, when requested,
    sends only a constant READY prompt and never any device or user content.
    """
    routes: dict[tuple[str, str | None], dict[str, Any]] = {}
    for role, primary in config.iter_primary_models():
        for model, required, name in (
            (primary, True, role),
            (primary.fallback, False, role + ".fallback"),
        ):
            if model.provider != "codex":
                continue
            entry = routes.setdefault(
                (model.model, model.reasoning_effort),
                {
                    "provider": "codex",
                    "model": model.model,
                    "effort": model.reasoning_effort,
                    "required": False,
                    "roles": [],
                    "valid": False,
                    "inference_tested": False,
                },
            )
            entry["required"] |= required
            entry["roles"].append(name)
    if not routes or os.getenv("ARTEMIS_FAKE_LLM") == "1":
        return {"ok": True, "checks": [], "scope": "codex", "inference_tested": False}
    binary = find_codex_binary()
    checks = list(routes.values())
    if not binary:
        return {
            "ok": False,
            "checks": checks,
            "error": "Codex CLI is not installed.",
            "scope": "codex",
        }
    client = CodexAppServerClient(binary)
    catalog_started = time.monotonic()
    try:
        try:
            models = await asyncio.wait_for(_catalog(client, force=force), timeout=20)
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            return {
                "ok": False,
                "checks": checks,
                "scope": "codex",
                "error": f"Codex model catalog unavailable ({type(exc).__name__}); retry diagnostics.",
            }
        catalog_seconds = round(time.monotonic() - catalog_started, 3)
        catalog = {m["model"]: m for m in models if m.get("model")}
        default = next((m for m in models if m.get("isDefault")), {})
        for check in checks:
            model = (
                default
                if check["model"] in {"auto", "default"}
                else catalog.get(check["model"], {})
            )
            if not model:
                check["message"] = (
                    "Model is absent from this Codex client's catalog; select a listed model."
                )
                continue
            check["resolved_model"] = model["model"]
            efforts = {e.get("reasoningEffort") for e in model.get("supportedReasoningEfforts", [])}
            if (
                check["effort"]
                and check["effort"] != "none"
                and efforts
                and check["effort"] not in efforts
            ):
                check["message"] = "Configured reasoning effort is not supported by this model."
                continue
            check.update(
                valid=True, message="Listed in the client catalog; inference access was not tested."
            )
        if verify_inference:
            # One minimal turn per model: node-specific effort support was checked above.
            results: dict[str, tuple[bool, str]] = {}
            durations: dict[str, float] = {}
            for check in checks:
                if not check["valid"]:
                    continue
                model = check["resolved_model"]
                if model not in results:
                    started = time.monotonic()
                    efforts = {
                        e.get("reasoningEffort")
                        for e in catalog[model].get("supportedReasoningEfforts", [])
                    }
                    try:
                        response = await asyncio.wait_for(
                            client.run_completion(
                                model=model,
                                effort="low" if "low" in efforts else None,
                                cwd=str(Path.cwd()),
                                base_instructions="Return only the requested JSON. Do not use tools or read files.",
                                developer_instructions="Connectivity check only.",
                                inputs=[{"type": "text", "text": 'Return {"status":"READY"}.'}],
                                output_schema={
                                    "type": "object",
                                    "properties": {"status": {"type": "string", "enum": ["READY"]}},
                                    "required": ["status"],
                                    "additionalProperties": False,
                                },
                                timeout_seconds=30,
                            ),
                            timeout=35,
                        )
                        import json

                        valid = (
                            json.loads(response["text"]).get("status") == "READY"
                            and response["model"] == model
                        )
                        results[model] = (
                            valid,
                            "Minimal inference passed."
                            if valid
                            else "Unexpected inference response or model substitution.",
                        )
                    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
                        # Provider errors can contain request data. Expose the category, not raw text.
                        results[model] = (
                            False,
                            f"Minimal inference failed ({classify_failure(exc).category.value}).",
                        )
                    durations[model] = round(time.monotonic() - started, 3)
                check["valid"], check["message"] = results[model]
                check["inference_tested"] = True
                check["inference_seconds"] = durations[model]
        return {
            "ok": all(c["valid"] for c in checks if c["required"]),
            "scope": "codex",
            "checks": checks,
            "available_models": list(catalog),
            "catalog_seconds": catalog_seconds,
            "inference_tested": any(c["inference_tested"] for c in checks),
        }
    finally:
        await client.close()


async def require_available_models(config: LLMConfig) -> dict[str, Any]:
    report = await preflight_models(config)
    if not report["ok"]:
        failed = [c["model"] for c in report["checks"] if c["required"] and not c["valid"]]
        raise ModelPreflightError(
            report.get("error") or "Codex model preflight failed: " + ", ".join(failed)
        )
    return report
