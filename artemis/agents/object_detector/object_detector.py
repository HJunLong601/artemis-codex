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

"""Universal Multi-Model Object Detector for Artemis."""

import asyncio
import base64
import json
import time
import os
from pathlib import Path
import re

from langchain_core.messages import HumanMessage, ToolMessage

from artemis.llm.structured import ParseFailure, parse_structured
from artemis.services.llm import get_llm
from artemis.utils.visual_location_cache import (
    cache_config_for_context,
    configured_cache,
    scope_for_state,
)
from artemis.utils.visual_page_elements import inventory_instruction, page_name_instruction
from third_party.mobile_use.utils.logger import get_logger

logger = get_logger(__name__)

_DETECTOR_SEMAPHORE = asyncio.Semaphore(6)


async def _detect_single_label(
    llm,
    ctx,
    image_bytes,
    mime_type,
    label,
    templates,
    timeout_val=None,
    inventory=None,
    inventory_timeout=None,
    page_metadata=None,
) -> list[dict]:
    if timeout_val is None:
        timeout_val = float(os.environ.get("OBJECT_DETECTOR_TIMEOUT", "10.0"))

    img_b64 = base64.b64encode(image_bytes).decode("utf-8")

    for i, template in enumerate(templates):
        prompt = template.replace("{labels_str}", label)
        collecting = inventory is not None and i == 0
        if collecting:
            prompt += inventory_instruction() + (
                '\nReturn an object {"detected": [...], "common_elements": [...]} instead of a list. '
                "The detected list keeps the original target point convention [y,x]; "
                "common_elements uses coords [x,y] and bounds [left,top,right,bottom]."
            )
        if page_metadata is not None:
            prompt += page_name_instruction() + (
                "\nReturn an object with detected (the original target list), page_name"
                " and, only if requested, common_elements. Keep detected point coordinates [y,x]."
            )
        messages = [
            HumanMessage(
                content=[
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{img_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ]
            )
        ]

        try:
            logger.info(f"Calling Object Detector for '{label}' (Attempt {i + 1})...")
            async with _DETECTOR_SEMAPHORE:
                response = await asyncio.wait_for(
                    llm.ainvoke(messages),
                    timeout=inventory_timeout if collecting and inventory_timeout else timeout_val,
                )

            output = response.content if isinstance(response.content, str) else ""
            if isinstance(response.content, list):
                output = "".join(
                    b.get("text", "")
                    for b in response.content
                    if isinstance(b, dict) and "text" in b
                )
            logger.info(f"Received response for '{label}' (Attempt {i + 1})")

            res_json = parse_structured(output)
            if isinstance(res_json, dict) and (inventory is not None or page_metadata is not None):
                common = res_json.get("common_elements")
                if inventory is not None and isinstance(common, list):
                    inventory[:] = common
                    if page_metadata is not None:
                        page_metadata["inventory_received"] = True
                if page_metadata is not None:
                    page_metadata["page_name"] = res_json.get("page_name")
                res_json = res_json.get("detected")
            if isinstance(res_json, list):
                valid_results = []
                for item in res_json:
                    if isinstance(item, dict):
                        item["label"] = label
                        valid_results.append(item)
                return valid_results
            if isinstance(res_json, ParseFailure):
                logger.warning(
                    f"Object detector response for '{label}' was not valid"
                    f" JSON (attempt {i + 1}): {res_json.error}"
                )
            else:
                logger.warning(
                    f"Object detector response for '{label}' parsed to"
                    f" {type(res_json).__name__} instead of a list (attempt {i + 1})."
                )

        except Exception as e:
            logger.warning(f"Detection attempt {i + 1} failed for '{label}': {e}")

    return []


async def _run_object_detection(
    ctx,
    image_bytes: bytes | str | Path | None = None,
    queries: list[str] | None = None,
    templates: list[str] | None = None,
    mime_type: str = "image/jpeg",
    global_timeout: float = 30.0,
    image_path: str | Path | None = None,
    *,
    state=None,
    cache_scope=None,
    bypass_cache: bool = False,
) -> dict:
    target_img = image_bytes if image_bytes is not None else image_path
    if target_img is None:
        raise ValueError(
            "Either image_bytes or image_path must be provided to _run_object_detection"
        )
    if isinstance(target_img, (str, Path)):
        image_data = Path(target_img).read_bytes()
    else:
        image_data = target_img

    queries = queries or []
    templates = templates or ["Point to the following objects: {labels_str}"]
    resolved = []
    cached_hits = 0
    # Legacy/direct detection tools share the same XML-first ordering. Crops,
    # annotations and historical frames cannot borrow the live-screen scope.
    if state is not None and str(target_img) == getattr(state, "latest_screenshot", None):
        from artemis.agents.explorer.geometry import pixel_to_norm, resolve_screen_size
        from artemis.agents.explorer.screen_index import ScreenIndex

        width, height = resolve_screen_size(ctx, state)
        index = ScreenIndex.from_hierarchy(state.latest_ui_hierarchy, width, height)
        remaining = []
        for query in queries:
            hits = index.exact_matches(query)
            if len(hits) == 1:
                resolved.append(
                    {
                        "label": query,
                        "point": list(pixel_to_norm(*hits[0].center, width, height)),
                        "source": hits[0].source,
                    }
                )
            else:
                remaining.append(query)
        queries = remaining
        if queries and cache_scope is None:
            cache_scope = await scope_for_state(ctx, state, target_img)
        incident = getattr(state, "open_incident", None)
        bypass_cache = bypass_cache or (isinstance(incident, dict) and bool(incident))
    cache = configured_cache(ctx) if cache_scope and queries else None
    namespace = "detector:" + json.dumps(templates, ensure_ascii=False)
    if cache:
        if bypass_cache:
            await asyncio.to_thread(cache.invalidate, cache_scope, queries)
            values = {}
        else:
            values = await asyncio.to_thread(
                cache.get_many,
                cache_scope,
                queries,
                namespace,
                ctx=ctx,
                frame=getattr(state, "latest_screenshot", None),
            )
        for query, value in values.items():
            resolved.append({"label": query, "point": value["coords"]})
        cached_hits = len(values)
        queries = [query for query in queries if query not in values]
    if not queries:
        answer = {"detected": resolved, "failed": []}
        if cached_hits:
            answer["location_cache_hits"] = cached_hits
        return answer
    try:
        llm = get_llm(ctx, name="object_detector")
    except Exception:
        llm = get_llm(ctx, name="operator")

    raw_timeout = getattr(getattr(ctx, "llm_config", None), "timeout", None)
    if isinstance(raw_timeout, (int, float)):
        timeout_val = float(raw_timeout)
    else:
        timeout_val = float(os.environ.get("OBJECT_DETECTOR_TIMEOUT", "10.0"))

    lease = (
        await asyncio.to_thread(cache.claim_page_elements, cache_scope)
        if cache and not bypass_cache and cache_config_for_context(ctx).batch_prefetch
        else None
    )
    inventory = [] if lease else None
    visual_started = time.perf_counter()
    # The longer answer may need more than the single-point default. Keep the
    # caller's total deadline and reserve one normal attempt; retries drop the
    # optional inventory so it cannot prevent recovering the requested target.
    inventory_timeout = max(timeout_val, global_timeout - timeout_val)
    page_metadata = {} if cache and not bypass_cache else None
    tasks = [
        asyncio.create_task(
            _detect_single_label(
                llm,
                ctx,
                image_data,
                mime_type,
                label=query,
                templates=templates,
                timeout_val=timeout_val,
                inventory=inventory if index == 0 else None,
                inventory_timeout=inventory_timeout,
                page_metadata=page_metadata if index == 0 else None,
            )
        )
        for index, query in enumerate(queries)
    ]

    try:
        done, pending = await asyncio.wait(tasks, timeout=global_timeout)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if cache and lease:
            await asyncio.to_thread(
                cache.put_page_elements, cache_scope, [], failed=True, lease=lease
            )
        raise
    finally:
        if cache:
            await asyncio.to_thread(
                cache.record_metric,
                cache_scope,
                "visual_batches",
                elapsed_ms=(time.perf_counter() - visual_started) * 1000,
            )

    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    fused_results = []
    for task in done:
        try:
            res = task.result()
            if res:
                fused_results.extend(res)
        except Exception as e:
            logger.error(f"Task raised exception: {e}")

    # Swap coordinates from [y, x] normalized to [x, y] normalized if needed
    for item in fused_results:
        if (
            isinstance(item, dict)
            and "point" in item
            and isinstance(item["point"], list)
            and len(item["point"]) == 2
        ):
            y_norm, x_norm = item["point"]
            item["point"] = [x_norm, y_norm]

    if cache and not bypass_cache:
        if inventory is not None:
            await asyncio.to_thread(
                cache.put_page_elements,
                cache_scope,
                inventory,
                failed=not (page_metadata or {}).get("inventory_received"),
                lease=lease,
            )
        for query in queries:
            matches = [
                item
                for item in fused_results
                if isinstance(item, dict) and item.get("label") == query
            ]
            if len(matches) == 1:
                await asyncio.to_thread(
                    cache.put,
                    cache_scope,
                    query,
                    namespace,
                    {
                        "coords": matches[0].get("point"),
                        "description": query,
                    },
                )

        if page_metadata:
            await asyncio.to_thread(
                cache.set_page_name, cache_scope, page_metadata.get("page_name")
            )

    fused_results = [*resolved, *fused_results]

    detected_labels = set(
        item["label"] for item in fused_results if isinstance(item, dict) and "label" in item
    )
    failed_queries = [query for query in queries if query not in detected_labels]

    result_dict = {"detected": fused_results, "failed": failed_queries}
    if cached_hits:
        result_dict["location_cache_hits"] = cached_hits

    if failed_queries:
        result_dict["message"] = (
            "Detection failed for some targets. Please try changing to a more"
            " detailed description (e.g., describe its shape, color, or nearby"
            " text)."
        )

    logger.info("Fused results from parallel inference.")
    return result_dict


async def _create_error_command(ctx, state, tool_call_id, error_message, wrapper):
    return ToolMessage(
        tool_call_id=tool_call_id or "default_tool_call",
        content=wrapper.on_failure_fn(error_message),
        additional_kwargs={"error": error_message},
        status="error",
    )


async def _create_success_command(ctx, state, tool_call_id, output, wrapper):
    return ToolMessage(
        tool_call_id=tool_call_id or "default_tool_call",
        content=wrapper.on_success_fn(output),
        status="success",
    )
