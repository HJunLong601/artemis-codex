#!/usr/bin/env python3
"""Paired native WebP Q70 versus aspect-preserving 720px-wide WebP Q70.

Uses already ARTEMIS-verified PNGs and the real Artemis Codex adapter. Each
completed ephemeral thread is unsubscribed outside the timing interval. No device
actions, automatic retries, model fallback, or production configuration edits.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import importlib.metadata
import io
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from scripts.benchmark_image_inputs import (
    CodexAppServerChatModel,
    ScreenRead,
    Variant,
    digest,
    image_info,
    load_manifest,
    messages_for,
    provider,
    save_json,
    score,
)
from scripts.benchmark_png_webp import native_limits

VARIANTS = [
    Variant("webp_native_q70", "WEBP", quality=70, method=4),
    Variant("webp_720_q70", "WEBP", quality=70, method=4),
]


def convert(raw, width=0):
    """Includes PNG decode, RGB conversion, optional Lanczos resize, and encoding."""
    with Image.open(io.BytesIO(raw)) as source:
        image = source.convert("RGB")
        if width and image.width > width:
            height = round(image.height * width / image.width)
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=70, method=4, lossless=False)
    return output.getvalue()


def prepare(args, manifest):
    local = []
    for case in manifest["cases"]:
        original = (args.manifest.parent / case["file"]).read_bytes()
        for variant in VARIANTS:
            width = 720 if variant.name == "webp_720_q70" else 0
            convert(original, width)
            times = []
            for _ in range(args.local_repeats):
                start = time.perf_counter()
                raw = convert(original, width)
                times.append((time.perf_counter() - start) * 1000)
            dest = args.output / "inputs" / case["id"]
            dest.mkdir(parents=True, exist_ok=True)
            (dest / (variant.name + ".webp")).write_bytes(raw)
            effective, _, changed = provider._prepare_image_bytes(raw, ".webp")
            local.append(
                {
                    "case": case["id"],
                    "variant": variant.name,
                    "image": image_info(raw),
                    "conversion_ms": statistics.median(times),
                    "conversion_repetitions_ms": times,
                    "default_adapter_changed": changed,
                    "default_adapter_image": image_info(effective),
                }
            )
    return local


def summarize(report):
    report["summary"] = []
    for variant in VARIANTS:
        rows = [r for r in report["rows"] if r["variant"] == variant.name]
        valid = [r for r in rows if "error" not in r]
        local = [r for r in report["local"] if r["variant"] == variant.name]
        summary = {
            "variant": variant.name,
            "attempts": len(rows),
            "successful": len(valid),
            "errors": len(rows) - len(valid),
            "total_bytes": sum(r["image"]["bytes"] for r in local),
            "median_kib": statistics.median(r["image"]["bytes"] / 1024 for r in local),
            "median_conversion_ms": statistics.median(r["conversion_ms"] for r in local),
            "ocr_correct": sum(r["ocr_correct"] for r in valid),
            "ocr_total": len(valid) * 3,
            "target_hits": sum(r["target_hit"] for r in valid),
            "passed": sum(r["passed"] for r in rows),
        }
        if valid:
            for key in ["request_seconds", "total_seconds", "conversion_seconds"]:
                summary["median_" + key] = statistics.median(r[key] for r in valid)
            usage = [r["usage"] for r in valid if r.get("usage")]
            for key in ["input_tokens", "output_tokens"]:
                if usage:
                    summary["median_" + key] = statistics.median(r[key] for r in usage)
        report["summary"].append(summary)


async def close_clients():
    clients = provider._clients.pop(asyncio.get_running_loop(), {})
    await asyncio.gather(*(c.close() for c in clients.values()), return_exceptions=True)


async def release_response(response):
    thread = response.response_metadata.get("thread_id")
    if not thread:
        raise RuntimeError("Missing thread ID; cannot safely release test session")
    clients = provider._clients.get(asyncio.get_running_loop(), {})
    if len(clients) != 1:
        raise RuntimeError("Expected exactly one benchmark App Server client")
    result = await asyncio.wait_for(
        next(iter(clients.values())).request("thread/unsubscribe", {"threadId": thread}), 20
    )
    if result.get("status") not in {"unsubscribed", "notLoaded"}:
        raise RuntimeError(f"Unexpected thread release status: {result}")
    return result


async def run(args):
    args.manifest = args.manifest.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.manifest)
    binary = provider.find_codex_binary(args.codex_binary)
    identity = {
        "model": args.model,
        "effort": args.effort,
        "codex_binary": binary,
        "codex_version": subprocess.check_output([binary, "--version"], text=True).strip(),
        "repeats": args.repeats,
        "local_repeats": args.local_repeats,
        "width": 720,
        "resize": "Lanczos, aspect ratio preserved, no upscale",
        "webp_quality": 70,
        "webp_method": 4,
        "lossless": False,
        "manifest_sha256": digest(args.manifest.read_bytes()),
        "source_hashes": {
            c["id"]: digest((args.manifest.parent / c["file"]).read_bytes())
            for c in manifest["cases"]
        },
        "script_sha256": digest(Path(__file__).read_bytes()),
        "shared_script_sha256": digest((ROOT / "scripts/benchmark_image_inputs.py").read_bytes()),
        "native_script_sha256": digest((ROOT / "scripts/benchmark_png_webp.py").read_bytes()),
        "provider_version": importlib.metadata.version("codex-client-provider"),
        "provider_sha256": digest(Path(provider.__file__).read_bytes()),
        "pillow": Image.__version__,
        "timeout": args.timeout,
        "session_cleanup": "thread/unsubscribe after every response, outside measured interval",
    }
    result_path = args.output / "results.json"
    if result_path.exists():
        report = json.loads(result_path.read_text(encoding="utf-8"))
        if report["identity"] != identity:
            raise ValueError("Resume parameters changed; choose a fresh output directory")
    else:
        report = {
            "identity": identity,
            "manifest": manifest,
            "local": prepare(args, manifest),
            "rows": [],
            "warmups": [],
            "started_at": datetime.now(UTC).isoformat(),
            "scope": "Contemporaneous native 1080x2400 WebP Q70 vs resized 720x1600 WebP Q70. "
            "Both start with original PNG preloaded; total includes PNG decode, resize, "
            "WebP encode, base64, real adapter, App Server, upload, queue, inference and output. "
            "No capture/disk read or result write/session cleanup in timing. No retries/fallback. "
            "Native input preserved using process-local limits; 720 input also passes default adapter unchanged.",
        }
        save_json(result_path, report)
    llm = CodexAppServerChatModel(
        image_preprocessing_enabled=False,  # Preserve the explicit benchmark variants.
        model_name=args.model,
        reasoning_effort=args.effort,
        timeout_seconds=args.timeout,
        codex_binary=binary,
    ).bind_tools([ScreenRead], tool_choice="required")
    done = {(r["repeat"], r["case"], r["variant"]) for r in report["rows"]}
    cases = list(manifest["cases"])
    random.Random(20261010).shuffle(cases)
    edge = max(max(c["width"], c["height"]) for c in cases)
    try:
        with native_limits(edge, 16 * 1024 * 1024):
            for local in report["local"]:
                raw = (
                    args.output / "inputs" / local["case"] / (local["variant"] + ".webp")
                ).read_bytes()
                effective, _, changed = provider._prepare_image_bytes(raw, ".webp")
                if changed or effective != raw:
                    raise RuntimeError("Adapter altered payload; invalid comparison")
            case = cases[0]
            raw = convert((args.manifest.parent / case["file"]).read_bytes(), 720)
            start = time.perf_counter()
            response = await asyncio.wait_for(
                llm.ainvoke(messages_for(case, raw, VARIANTS[1])), args.timeout + 10
            )
            report["warmups"].append(
                {"seconds": time.perf_counter() - start, "usage": response.usage_metadata}
            )
            report["warmups"][-1]["cleanup"] = await release_response(response)
            save_json(result_path, report)
            for repeat in range(1, args.repeats + 1):
                for case in cases if repeat % 2 else list(reversed(cases)):
                    order = (
                        VARIANTS if (cases.index(case) + repeat) % 2 else list(reversed(VARIANTS))
                    )
                    original = (args.manifest.parent / case["file"]).read_bytes()
                    for variant in order:
                        if (repeat, case["id"], variant.name) in done:
                            continue
                        row = {
                            "repeat": repeat,
                            "case": case["id"],
                            "variant": variant.name,
                            "started_at": datetime.now(UTC).isoformat(),
                        }
                        response = None
                        start = time.perf_counter()
                        raw = convert(original, 720 if variant == VARIANTS[1] else 0)
                        converted = time.perf_counter()
                        try:
                            response = await asyncio.wait_for(
                                llm.ainvoke(messages_for(case, raw, variant)), args.timeout + 10
                            )
                            if response.response_metadata.get("model") != args.model:
                                raise RuntimeError("Returned model does not match requested model")
                            calls = response.tool_calls
                            decision = (
                                calls[0]["args"]
                                if len(calls) == 1 and calls[0]["name"] == "ScreenRead"
                                else {}
                            )
                            row.update(
                                decision=decision,
                                usage=response.usage_metadata,
                                telemetry=response.response_metadata,
                                **score(case, decision),
                            )
                        except Exception as exc:
                            row.update(
                                error=type(exc).__name__, message=str(exc), **score(case, {})
                            )
                        end = time.perf_counter()
                        row.update(
                            conversion_seconds=converted - start,
                            request_seconds=end - converted,
                            total_seconds=end - start,
                            submitted_image=image_info(raw),
                        )
                        expected = next(
                            x["image"]["sha256"]
                            for x in report["local"]
                            if x["case"] == case["id"] and x["variant"] == variant.name
                        )
                        if row["submitted_image"]["sha256"] != expected:
                            raise RuntimeError("Non-reproducible payload")
                        report["rows"].append(row)
                        summarize(report)
                        save_json(result_path, report)
                        print(
                            json.dumps(
                                {
                                    k: row[k]
                                    for k in [
                                        "repeat",
                                        "case",
                                        "variant",
                                        "request_seconds",
                                        "total_seconds",
                                        "passed",
                                    ]
                                }
                            ),
                            flush=True,
                        )
                        if response is not None:
                            row["cleanup"] = await release_response(response)
                            save_json(result_path, report)
                        if "error" in row:
                            raise RuntimeError(
                                "Inference failed; stop and diagnose, preserve attempt"
                            )
            report["completed_at"] = datetime.now(UTC).isoformat()
    except Exception as exc:
        report.setdefault("run_errors", []).append(
            {"at": datetime.now(UTC).isoformat(), "error": type(exc).__name__, "message": str(exc)}
        )
        raise
    finally:
        summarize(report)
        save_json(result_path, report)
        await close_clients()
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--codex-binary")
    p.add_argument("--model", default="gpt-6.1-sol")
    p.add_argument("--effort", default="medium")
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--local-repeats", type=int, default=10)
    p.add_argument("--timeout", type=float, default=90)
    args = p.parse_args()
    if min(args.repeats, args.local_repeats) < 1 or args.timeout <= 0:
        p.error("Positive repetitions and timeout required")
    asyncio.run(run(args))
