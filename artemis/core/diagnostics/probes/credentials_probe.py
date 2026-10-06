# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""LLM & Multimodal Vision Credentials Readiness Probe."""

import asyncio
from typing import Any
from artemis.config import settings
from artemis.core.diagnostics.probes.base import BaseProbe
from artemis.core.diagnostics.schema import (
    ProbeAction,
    ProbeCategory,
    ProbeResult,
    ProbeStatus,
)


class LLMCredentialsProbe(BaseProbe):
    """Probe verifying Gemini and multi-provider multimodal LLM credentials."""

    @property
    def probe_id(self) -> str:
        return "gemini_api_key"

    @property
    def category(self) -> ProbeCategory:
        return ProbeCategory.CREDENTIALS

    @property
    def is_blocker(self) -> bool:
        return True

    def _mask_key(self, key_str: str) -> str:
        """Helper to safely mask an API credential for display."""
        if len(key_str) > 10:
            return f"{key_str[:6]}...{key_str[-4:]}"
        return "***"

    async def probe(self) -> ProbeResult:
        import os

        from artemis.config.settings import is_placeholder_key

        gemini_key = settings.get_api_key("google")
        openai_key = settings.get_api_key("openai")
        claude_key = settings.get_api_key("anthropic")
        openrouter_key = settings.get_api_key("openrouter")
        xai_key = settings.get_api_key("xai")
        ocr_key = settings.get_api_key("ocr")

        configured_providers: list[dict[str, Any]] = []
        api_keys_map: dict[str, str] = {}

        from artemis.config.llm import parse_llm_config
        from artemis.llm.codex_app_server import codex_client_status

        try:
            config = parse_llm_config()
        except Exception as exc:
            return ProbeResult(
                id=self.probe_id,
                category=self.category,
                title="Multimodal LLM Credentials",
                status=ProbeStatus.FAIL,
                is_blocker=True,
                summary="Model routing unavailable",
                description="Cannot determine required credentials until the model configuration is valid.",
                metadata={"configured_count": 0, "routing_error": type(exc).__name__},
                actions=[
                    ProbeAction(
                        action_type="hint",
                        label="Fix model configuration",
                        payload="Resolve the System Configuration check and run diagnostics again.",
                    )
                ],
            )

        active_provider = config.planner.provider
        models = {}
        roles: dict[str, list[str]] = {}
        for name, model in config.iter_primary_models():
            models.setdefault(model.provider, model)
            roles.setdefault(model.provider, []).append(name)

        # Refresh the public CLI status, never read or copy authentication tokens.
        if "codex" in models:
            codex_client_status.cache_clear()
        outcomes = await asyncio.gather(
            *(
                asyncio.to_thread(model.validate_provider, roles[provider][0])
                for provider, model in models.items()
            ),
            return_exceptions=True,
        )
        route_checks = []
        for provider, outcome in zip(models, outcomes):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            valid = not isinstance(outcome, BaseException)
            credential_type = (
                "client_session"
                if provider == "codex"
                else "application_default"
                if provider == "vertexai"
                else "endpoint"
                if provider in {"custom", "ollama", "vllm"}
                else "api_key"
            )
            route_checks.append(
                {
                    "provider": provider,
                    "label": provider,
                    "required": True,
                    "roles": roles[provider],
                    "valid": valid,
                    "credential_type": credential_type,
                    "message": str(outcome)
                    if not valid
                    else (
                        "Codex CLI login is available; model inference was not tested."
                        if provider == "codex"
                        else "Required credential configuration is present; API access and model inference were not tested."
                    ),
                }
            )
            if valid and provider == "codex":
                configured_providers.append(
                    {
                        "provider": "codex",
                        "label": "Codex client",
                        "masked": "CLI login",
                        "credential_type": "client_session",
                    }
                )
        if gemini_key and not is_placeholder_key(gemini_key):
            g_val = gemini_key.get_secret_value()
            configured_providers.append(
                {
                    "provider": "google",
                    "label": "Gemini",
                    "masked": self._mask_key(g_val),
                    "raw_key": g_val,
                    "key": g_val,
                }
            )
            api_keys_map["google"] = g_val
            api_keys_map["gemini"] = g_val
        if openai_key and not is_placeholder_key(openai_key):
            o_val = openai_key.get_secret_value()
            configured_providers.append(
                {
                    "provider": "openai",
                    "label": "ChatGPT",
                    "masked": self._mask_key(o_val),
                    "raw_key": o_val,
                    "key": o_val,
                }
            )
            api_keys_map["openai"] = o_val
        if claude_key and not is_placeholder_key(claude_key):
            c_val = claude_key.get_secret_value()
            configured_providers.append(
                {
                    "provider": "anthropic",
                    "label": "Claude",
                    "masked": self._mask_key(c_val),
                    "raw_key": c_val,
                    "key": c_val,
                }
            )
            api_keys_map["anthropic"] = c_val
        if openrouter_key and not is_placeholder_key(openrouter_key):
            or_val = openrouter_key.get_secret_value()
            configured_providers.append(
                {
                    "provider": "openrouter",
                    "label": "OpenRouter",
                    "masked": self._mask_key(or_val),
                    "raw_key": or_val,
                    "key": or_val,
                }
            )
            api_keys_map["openrouter"] = or_val
        if xai_key and not is_placeholder_key(xai_key):
            x_val = xai_key.get_secret_value()
            configured_providers.append(
                {
                    "provider": "xai",
                    "label": "xAI (Grok)",
                    "masked": self._mask_key(x_val),
                    "raw_key": x_val,
                    "key": x_val,
                }
            )
            api_keys_map["xai"] = x_val

        # Detect any custom model endpoints or environment variables defined in files
        for env_var, label, prov_id in [
            ("DEEPSEEK_API_KEY", "DeepSeek", "deepseek"),
            ("GROQ_API_KEY", "Groq", "groq"),
            ("OPENAI_BASE_URL", "Custom OpenAI Endpoint", "custom"),
            ("OLLAMA_BASE_URL", "Local Ollama", "ollama"),
            ("VLLM_BASE_URL", "vLLM Endpoint", "vllm"),
            ("VERTEX_AI_PROJECT", "Google Cloud Vertex AI", "vertexai"),
        ]:
            val = os.environ.get(env_var)
            if val and val.strip() and not is_placeholder_key(val.strip()):
                configured_providers.append(
                    {
                        "provider": prov_id,
                        "label": label,
                        "masked": self._mask_key(val.strip()),
                        "raw_key": val.strip(),
                        "key": val.strip(),
                    }
                )
                api_keys_map[prov_id] = val.strip()

        if ocr_key and not is_placeholder_key(ocr_key):
            api_keys_map["ocr"] = ocr_key.get_secret_value()

        available = {entry["provider"]: entry for entry in configured_providers}
        for entry in configured_providers:
            entry["required"] = entry["provider"] in roles
            entry["roles"] = roles.get(entry["provider"], [])
        for check in route_checks:
            check["label"] = available.get(check["provider"], {}).get("label", check["provider"])
            for value in api_keys_map.values():
                if value:
                    check["message"] = check["message"].replace(value, "***")
        current_active_key = available.get(active_provider, {}).get("key", "")
        metadata = {
            "active_provider": active_provider,
            "required_providers": list(roles),
            "provider_checks": route_checks,
            "configured_count": len(configured_providers),
            "providers": configured_providers,
            "has_ocr_key": ocr_key is not None,
            "current_key": current_active_key,
            "current_gemini_key": gemini_key.get_secret_value() if gemini_key else "",
            "api_keys": api_keys_map,
        }
        failures = [check for check in route_checks if not check["valid"]]
        metadata["missing_providers"] = [check["provider"] for check in failures]
        actions = []
        if "codex" in metadata["missing_providers"]:
            actions.extend(
                [
                    ProbeAction(
                        action_type="command",
                        label="Check Codex login",
                        payload="codex login status",
                    ),
                    ProbeAction(
                        action_type="hint",
                        label="Check the execution environment",
                        payload=(
                            "Compare Codex login status in the normal user environment and the MCP server "
                            "environment, including CODEX_HOME and the selected CLI. A sandbox may not "
                            "have access to the user's login. If the normal user environment is also "
                            "signed out, run codex login there; then restart the MCP server and re-run "
                            "mobile_diagnose. Do not copy authentication tokens into the sandbox or chat."
                        ),
                    ),
                ]
            )
        if "vertexai" in metadata["missing_providers"]:
            actions.append(
                ProbeAction(
                    action_type="hint",
                    label="Check Vertex AI credentials",
                    payload="Configure Google Application Default Credentials and a Google Cloud project for Vertex AI.",
                )
            )
        missing_keys = [
            check["provider"] for check in failures if check["credential_type"] == "api_key"
        ]
        if missing_keys:
            actions.append(
                ProbeAction(
                    action_type="hint",
                    label="Configure required provider keys",
                    payload="Add credentials for "
                    + ", ".join(missing_keys)
                    + " to the local .env or MCP environment, then restart the server. Never paste keys into chat.",
                )
            )
        if failures:
            summary = (
                "Codex session unavailable"
                if metadata["missing_providers"] == ["codex"]
                else "Required credentials unavailable"
            )
            description = "; ".join(check["message"] for check in failures)
        else:
            summary = (
                "Active (Codex client)"
                if list(roles) == ["codex"]
                else "Required provider credentials configured"
            )
            description = (
                "Credentials are available for the configured primary routes: "
                + ", ".join(roles)
                + ". Model inference was not tested."
            )
        return ProbeResult(
            id=self.probe_id,
            category=self.category,
            title="Multimodal LLM Credentials",
            status=ProbeStatus.FAIL if failures else ProbeStatus.PASS,
            is_blocker=self.is_blocker,
            summary=summary,
            description=description,
            metadata=metadata,
            actions=actions,
        )


class VisionOCRProbe(BaseProbe):
    """Probe verifying optional Google Cloud Vision OCR credentials."""

    @property
    def probe_id(self) -> str:
        return "vision_ocr_key"

    @property
    def category(self) -> ProbeCategory:
        return ProbeCategory.CREDENTIALS

    @property
    def is_blocker(self) -> bool:
        return False

    def _mask_key(self, key_str: str) -> str:
        """Helper to safely mask an API credential for display."""
        if len(key_str) > 10:
            return f"{key_str[:6]}...{key_str[-4:]}"
        return "***"

    async def probe(self) -> ProbeResult:
        from artemis.utils.ocr_api import is_ocr_configured

        ocr_key = settings.get_api_key("ocr")
        is_configured = is_ocr_configured() and ocr_key is not None

        if is_configured and ocr_key:
            val = ocr_key.get_secret_value()
            masked = self._mask_key(val)
            return ProbeResult(
                id=self.probe_id,
                category=self.category,
                title="Vision OCR API Key (Optional)",
                status=ProbeStatus.PASS,
                is_blocker=False,
                summary="Active & Configured",
                description=f"Google Cloud Vision OCR ({masked}) is active for image text recognition.",
                metadata={"configured": True, "masked_key": masked, "key": val, "raw_key": val},
                actions=[
                    ProbeAction(
                        action_type="hint",
                        label="OCR Enabled",
                        payload="OCR is active and will fuse text with UI XML hierarchy.",
                    )
                ],
            )

        return ProbeResult(
            id=self.probe_id,
            category=self.category,
            title="Vision OCR API Key (Optional)",
            status=ProbeStatus.PASS,
            is_blocker=False,
            summary="Not Configured (Optional)",
            description="OCR_API_KEY is not set. Perception uses the UI XML hierarchy without OCR.",
            metadata={"configured": False},
            actions=[
                ProbeAction(
                    action_type="hint",
                    label="Standard XML Perception",
                    payload="Artemis uses pure UI layout parsing and Set-of-Marks visual grounding.",
                )
            ],
        )
