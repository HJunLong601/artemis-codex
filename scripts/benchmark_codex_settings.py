#!/usr/bin/env python3
"""Shadow benchmark on verified MIUI regression frames. Never executes device actions.

Requires the report and 01/02/03 JPEGs produced by real_device_settings_regression.py.
Compares semantic next-action decisions, not full Flash navigation or safety.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from artemis.llm.codex_app_server import CodexAppServerChatModel
import codex_client_provider.langchain as provider


class NextAction(BaseModel):
    """Return the next action for this observed screen; do not execute it."""

    action: str = Field(description="One of open_display, back, finish, uncertain")
    observed_labels: list[str] = Field(
        description="Exact visible Chinese labels supporting the decision"
    )


CASES = [
    ("01-settings.jpg", "任务刚开始，还没有打开显示。", "open_display", {"显示"}),
    (
        "02-display.jpg",
        "刚点击了设置首页的显示条目。",
        "back",
        {"浅色模式", "深色模式", "亮度", "护眼模式"},
    ),
    ("03-return.jpg", "此前已进入显示并确认标签，然后刚按了返回键。", "finish", {"设置"}),
]
VARIANTS = [("gpt-5.6-sol", "medium"), ("gpt-5.6-sol", "low"), ("gpt-5.6-luna", "low")]


async def run(args):
    source = Path(args.frames).resolve()
    acceptance = json.loads((source / "report.json").read_text(encoding="utf-8"))
    if (
        acceptance.get("status") != "passed"
        or len(acceptance.get("checks", [])) != 3
        or not all(c.get("passed") for c in acceptance["checks"])
    ):
        raise ValueError("Use frames from a passed three-check real-device regression.")
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    report = {
        "scope": "shadow semantic next-action decisions; no device actions; not a full Flash benchmark",
        "source": str(source),
        "device_id": acceptance["device_id"],
        "rows": rows,
    }
    for repeat in range(args.repeats):
        # Rotate order to reduce a systematic first-model cold-start bias.
        variants = VARIANTS[repeat % 3 :] + VARIANTS[: repeat % 3]
        for model, effort in variants:
            llm = CodexAppServerChatModel(
                model_name=model, reasoning_effort=effort, timeout_seconds=60
            ).bind_tools([NextAction], tool_choice="required")
            for filename, history, expected, labels in CASES:
                encoded = base64.b64encode((source / filename).read_bytes()).decode("ascii")
                messages = [
                    SystemMessage(
                        content="根据截图和已有步骤选择下一步，只调用 NextAction。目标：设置→显示，确认显示选项后返回设置首页并结束。不更改任何设置。拿不准时选 uncertain。"
                    ),
                    HumanMessage(
                        content=[
                            {"type": "text", "text": history},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                            },
                        ]
                    ),
                ]
                row = {
                    "repeat": repeat + 1,
                    "model": model,
                    "effort": effort,
                    "case": filename,
                    "passed": False,
                }
                started = time.monotonic()
                try:
                    response = await asyncio.wait_for(llm.ainvoke(messages), timeout=70)
                    calls = response.tool_calls
                    decision = (
                        calls[0]["args"]
                        if len(calls) == 1 and calls[0]["name"] == "NextAction"
                        else {}
                    )
                    observed = decision.get("observed_labels")
                    matches = (
                        labels.intersection(observed)
                        if isinstance(observed, list) and all(isinstance(x, str) for x in observed)
                        else set()
                    )
                    row.update(
                        passed=decision.get("action") == expected
                        and len(matches) >= (2 if expected == "back" else 1),
                        decision=decision,
                        usage=response.usage_metadata,
                        telemetry=response.response_metadata,
                    )
                except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                    # Fail the sample, never execute a candidate action or silently
                    # replace its model with a fallback that inflates success rates.
                    row["error"] = type(exc).__name__
                row["elapsed_seconds"] = round(time.monotonic() - started, 3)
                rows.append(row)
                output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    json.dumps(
                        {
                            k: row[k]
                            for k in (
                                "repeat",
                                "model",
                                "effort",
                                "case",
                                "passed",
                                "elapsed_seconds",
                            )
                        }
                    ),
                    flush=True,
                )
    report["summary"] = []
    for model, effort in VARIANTS:
        group = [r for r in rows if r["model"] == model and r["effort"] == effort]
        times = sorted(r["elapsed_seconds"] for r in group)
        report["summary"].append(
            {
                "model": model,
                "effort": effort,
                "passed": sum(r["passed"] for r in group),
                "samples": len(group),
                "median_seconds": round(statistics.median(times), 3),
                "max_seconds": max(times),
            }
        )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return all(r["passed"] for r in rows)


async def main(args):
    try:
        return await run(args)
    finally:
        # Drain this standalone benchmark's pooled subprocesses before its
        # Windows event loop closes. Other event loops keep their own clients.
        clients = provider._clients.pop(asyncio.get_running_loop(), {})
        if clients:
            await asyncio.gather(*(client.close() for client in clients.values()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        parser.error("--repeats must be between 1 and 10")
    sys.exit(0 if asyncio.run(main(args)) else 1)
