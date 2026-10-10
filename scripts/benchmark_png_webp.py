#!/usr/bin/env python3
"""Native PNG vs same-resolution lossy WebP Q70, using the actual Codex adapter.

Process-local limits preserve native image bytes; production settings are not
modified. Also records what the DEFAULT adapter would do to both formats.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime
import importlib.metadata
import io
import json
import os
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

PNG = Variant("png_original", "PNG")
WEBP = Variant("webp_q70", "WEBP", quality=70, method=4)


@contextmanager
def native_limits(edge, byte_limit):
    overrides = {
        "CODEX_CLIENT_IMAGE_MAX_EDGE": str(edge),
        "CODEX_CLIENT_IMAGE_MAX_BYTES": str(byte_limit),
    }
    previous = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for k, value in previous.items():
            if value is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = value


def convert(raw):
    # Timer includes decoding the original PNG, mode conversion, and WebP encoding.
    with Image.open(io.BytesIO(raw)) as source:
        image = source.convert("RGB")
        output = io.BytesIO()
        image.save(output, format="WEBP", quality=70, method=4, lossless=False)
    return output.getvalue()


def prepare(args, manifest):
    local = []
    for case in manifest["cases"]:
        png = (args.manifest.parent / case["file"]).read_bytes()
        if image_info(png)["format"] != "PNG":
            raise ValueError("Expected original lossless PNG screenshots")
        convert(png)  # untimed warm-up
        times = []
        for _ in range(args.local_repeats):
            started = time.perf_counter()
            webp = convert(png)
            times.append((time.perf_counter() - started) * 1000)
        dest = args.output / "inputs" / case["id"]
        dest.mkdir(parents=True, exist_ok=True)
        for variant, raw, suffix in [(PNG, png, ".png"), (WEBP, webp, ".webp")]:
            (dest / (variant.name + suffix)).write_bytes(raw)
            effective, effective_suffix, changed = provider._prepare_image_bytes(raw, suffix)
            local.append(
                {
                    "case": case["id"],
                    "variant": variant.name,
                    "image": image_info(raw),
                    "conversion_ms": statistics.median(times) if variant == WEBP else 0,
                    "conversion_repetitions_ms": times if variant == WEBP else [],
                    "default_adapter_changed": changed,
                    "default_adapter_image": image_info(effective),
                    "default_adapter_suffix": effective_suffix,
                }
            )
    return local


def update_summary(report):
    summaries = []
    for variant in [PNG, WEBP]:
        rows = [r for r in report["rows"] if r["variant"] == variant.name]
        valid = [r for r in rows if "error" not in r]
        local = [r for r in report["local"] if r["variant"] == variant.name]
        summary = {
            "variant": variant.name,
            "samples": len(rows),
            "errors": len(rows) - len(valid),
            "median_kib": statistics.median(r["image"]["bytes"] / 1024 for r in local),
            "total_bytes": sum(r["image"]["bytes"] for r in local),
            "median_conversion_ms": statistics.median(r["conversion_ms"] for r in local),
            "ocr_correct": sum(r["ocr_correct"] for r in rows),
            "ocr_total": len(rows) * 3,
            "target_hits": sum(r["target_hit"] for r in rows),
            "passed": sum(r["passed"] for r in rows),
        }
        if valid:
            for key in ["request_seconds", "total_seconds", "conversion_seconds"]:
                summary["median_" + key] = statistics.median(r[key] for r in valid)
            summary["median_input_tokens"] = statistics.median(
                r["usage"]["input_tokens"] for r in valid if r.get("usage")
            )
        summaries.append(summary)
    report["summary"] = summaries


async def run(args):
    args.manifest = args.manifest.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.manifest)
    edge = max(max(c["width"], c["height"]) for c in manifest["cases"])
    size_limit = max(
        16 * 1024 * 1024,
        max((args.manifest.parent / c["file"]).stat().st_size for c in manifest["cases"]) * 2,
    )
    identity = {
        "codex_binary": provider.find_codex_binary(args.codex_binary),
        "codex_version": subprocess.check_output(
            [provider.find_codex_binary(args.codex_binary), "--version"], text=True
        ).strip(),
        "model": args.model,
        "effort": args.effort,
        "repeats": args.repeats,
        "local_repeats": args.local_repeats,
        "manifest_sha256": digest(args.manifest.read_bytes()),
        "source_hashes": {
            c["id"]: digest((args.manifest.parent / c["file"]).read_bytes())
            for c in manifest["cases"]
        },
        "provider_version": importlib.metadata.version("codex-client-provider"),
        "provider_sha256": digest(Path(provider.__file__).read_bytes()),
        "script_sha256": digest(Path(__file__).read_bytes()),
        "shared_script_sha256": digest((ROOT / "scripts/benchmark_image_inputs.py").read_bytes()),
        "pillow": Image.__version__,
        "webp_quality": 70,
        "webp_method": 4,
        "max_edge": edge,
        "max_bytes": size_limit,
        "seed": 20261010,
        "timeout": args.timeout,
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
            "scope": "Original PNG bytes vs native-size lossy WebP quality=70 method=4. Process-local adapter limits preserve submitted bytes. PNG file read and preloaded in memory before timer for both routes. WebP total includes actual PNG decode+conversion, base64+adapter+App Server+network+model generation. No capture/navigation, no automatic retry/fallback. Upload-only time is not available from this provider.",
        }
        save_json(result_path, report)
    llm = CodexAppServerChatModel(
        image_preprocessing_enabled=False,  # Preserve the explicit benchmark variants.
        model_name=args.model,
        reasoning_effort=args.effort,
        timeout_seconds=args.timeout,
        codex_binary=args.codex_binary,
    ).bind_tools([ScreenRead], tool_choice="required")
    done = {(r["repeat"], r["case"], r["variant"]) for r in report["rows"]}
    cases = list(manifest["cases"])
    random.Random(20261010).shuffle(cases)
    try:
        with native_limits(edge, size_limit):
            # Assert the exact provider-bound bytes remain PNG and WebP for every case.
            for local in report["local"]:
                variant = PNG if local["variant"] == PNG.name else WEBP
                suffix = ".png" if variant == PNG else ".webp"
                raw = (
                    args.output / "inputs" / local["case"] / (variant.name + suffix)
                ).read_bytes()
                effective, _, changed = provider._prepare_image_bytes(raw, suffix)
                if changed or effective != raw:
                    raise RuntimeError("Adapter changed the test image; comparison invalid")
            # Each process invocation gets its own excluded warm-up, including resume.
            first = cases[0]
            raw = (args.manifest.parent / first["file"]).read_bytes()
            start = time.perf_counter()
            response = await asyncio.wait_for(
                llm.ainvoke(messages_for(first, raw, PNG)), args.timeout + 10
            )
            report["warmups"].append(
                {
                    "seconds": time.perf_counter() - start,
                    "usage": response.usage_metadata,
                    "started_at": datetime.now(UTC).isoformat(),
                }
            )
            save_json(result_path, report)
            for repeat in range(1, args.repeats + 1):
                for i, case in enumerate(cases if repeat % 2 else list(reversed(cases))):
                    original = (args.manifest.parent / case["file"]).read_bytes()
                    # Balanced AB/BA order on eight pages, reversing each page on repeat 2.
                    page_index = cases.index(case)
                    order = [PNG, WEBP] if (page_index + repeat) % 2 else [WEBP, PNG]
                    for variant in order:
                        key = (repeat, case["id"], variant.name)
                        if key in done:
                            continue
                        row = {
                            "repeat": repeat,
                            "case": case["id"],
                            "variant": variant.name,
                            "started_at": datetime.now(UTC).isoformat(),
                        }
                        start = time.perf_counter()
                        raw = convert(original) if variant == WEBP else original
                        converted = time.perf_counter()
                        try:
                            response = await asyncio.wait_for(
                                llm.ainvoke(messages_for(case, raw, variant)), args.timeout + 10
                            )
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
                                error=type(exc).__name__, message=str(exc)[:300], **score(case, {})
                            )
                        end = time.perf_counter()
                        row.update(
                            conversion_seconds=converted - start,
                            request_seconds=end - converted,
                            total_seconds=end - start,
                            submitted_image=image_info(raw),
                        )
                        if row["submitted_image"]["sha256"] != next(
                            x["image"]["sha256"]
                            for x in report["local"]
                            if x["case"] == case["id"] and x["variant"] == variant.name
                        ):
                            raise RuntimeError("Encoded payload was not reproducible")
                        report["rows"].append(row)
                        update_summary(report)
                        save_json(result_path, report)
                        print(
                            json.dumps(
                                {
                                    k: row[k]
                                    for k in [
                                        "repeat",
                                        "case",
                                        "variant",
                                        "conversion_seconds",
                                        "request_seconds",
                                        "total_seconds",
                                        "passed",
                                    ]
                                }
                            ),
                            flush=True,
                        )
            report["completed_at"] = datetime.now(UTC).isoformat()
    except Exception as exc:
        report.setdefault("run_errors", []).append(
            {"at": datetime.now(UTC).isoformat(), "type": type(exc).__name__, "message": str(exc)}
        )
        raise
    finally:
        update_summary(report)
        save_json(result_path, report)
        clients = provider._clients.pop(asyncio.get_running_loop(), {})
        if clients:
            await asyncio.gather(*(c.close() for c in clients.values()), return_exceptions=True)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--model", default="gpt-6.1-sol")
    p.add_argument("--codex-binary", help="Explicit Codex client executable; recorded in results")
    p.add_argument("--effort", default="medium")
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--local-repeats", type=int, default=10)
    p.add_argument("--timeout", type=float, default=90)
    args = p.parse_args()
    if min(args.repeats, args.local_repeats) < 1 or args.timeout <= 0:
        p.error("Positive repetitions/timeout required")
    asyncio.run(run(args))
