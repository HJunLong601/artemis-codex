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

"""Artemis integration for the reusable Codex client provider.

Protocol, process, multimodal, and LangChain behavior lives in the independently
published ``codex_client_provider`` package. This module keeps the original import path
stable and supplies Artemis-specific client identity and compatibility exports.
"""

from __future__ import annotations

from pydantic import Field

from codex_client_provider.langchain import (
    CODEX_IMAGE_JPEG_QUALITY,
    CODEX_IMAGE_MAX_BYTES,
    CODEX_IMAGE_MAX_EDGE,
    CODEX_IMAGE_MIN_JPEG_QUALITY,
    CodexAppServerChatModel as _CodexAppServerChatModel,
    CodexAppServerClient,
    CodexAppServerError,
    codex_client_status,
    find_codex_binary,
)


class CodexAppServerChatModel(_CodexAppServerChatModel):
    """Codex App Server model carrying Artemis identity in protocol metadata."""

    client_name: str = Field(default="artemis")
    client_title: str = Field(default="Artemis")
    client_version: str = Field(default="1.0")
    service_name: str = Field(default="artemis")


__all__ = [
    "CODEX_IMAGE_JPEG_QUALITY",
    "CODEX_IMAGE_MAX_BYTES",
    "CODEX_IMAGE_MAX_EDGE",
    "CODEX_IMAGE_MIN_JPEG_QUALITY",
    "CodexAppServerChatModel",
    "CodexAppServerClient",
    "CodexAppServerError",
    "codex_client_status",
    "find_codex_binary",
]
