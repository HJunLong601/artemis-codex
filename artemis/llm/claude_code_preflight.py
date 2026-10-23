"""Official Claude Code readiness; routine checks never perform inference."""

import re
import time

from artemis.llm.claude_code_client import probe_claude_code, run_claude_completion
from artemis.llm.reliability import classify_failure


async def preflight_claude_models(config, *, verify_inference=False):
    routes = {}
    for role, primary in config.iter_primary_models():
        for model, required, name in (
            (primary, True, role),
            (primary.fallback, False, role + ".fallback"),
        ):
            if model.provider != "claude_code":
                continue
            entry = routes.setdefault(
                (model.model, model.reasoning_effort),
                {
                    "provider": "claude_code",
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
    checks = list(routes.values())
    report = {
        "ok": True,
        "scope": "claude_code",
        "checks": checks,
        "catalog_available": False,
        "inference_tested": False,
    }
    if not checks:
        return report
    auth = await probe_claude_code()
    for check in checks:
        if auth["state"] != "authenticated":
            check["message"] = (
                "Claude Code is not ready ("
                + auth["state"]
                + "). Use the official CLI to check installation and authentication."
            )
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", check["model"]):
            check["message"] = "Invalid Claude Code model identifier."
            continue
        if check["effort"] not in {None, "none", "low", "medium", "high", "xhigh", "max"}:
            check["message"] = "Unsupported Claude Code reasoning effort."
            continue
        check["valid"] = True
        check["message"] = (
            "Claude Code authentication is available; model access and inference were not tested."
        )
        if not verify_inference:
            continue
        started = time.monotonic()
        check["inference_tested"] = True
        try:
            result = await run_claude_completion(
                binary=auth.get("binary"),
                model=check["model"],
                effort=check["effort"],
                system_prompt="Return only the requested JSON readiness response. No tools or device data.",
                content=[{"type": "text", "text": "Reply with READY."}],
                output_schema={
                    "type": "object",
                    "properties": {"status": {"type": "string", "const": "READY"}},
                    "required": ["status"],
                    "additionalProperties": False,
                },
                timeout=60,
            )
            check["valid"] = result.get("structured_output") == {"status": "READY"}
            check["message"] = (
                "Minimal inference passed."
                if check["valid"]
                else "Minimal inference did not return READY."
            )
            if isinstance(result.get("modelUsage"), dict):
                check["models_used"] = list(result["modelUsage"])
        except Exception as exc:
            check["valid"] = False
            check["message"] = f"Minimal inference failed ({classify_failure(exc).category.value})."
        check["inference_seconds"] = round(time.monotonic() - started, 3)
    report["ok"] = all(check["valid"] for check in checks if check["required"])
    report["inference_tested"] = any(check["inference_tested"] for check in checks)
    return report
