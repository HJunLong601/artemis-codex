#!/usr/bin/env python3
"""Paired model benchmark on fixed ARTEMIS-verified screenshots, using production image policy."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from artemis.llm.image_inputs import prepare_image_bytes, prepare_image_messages
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
from scripts.benchmark_webp_720p import close_clients, release_response
from scripts.report_png_webp import analyze

MODELS = ["gpt-6.1-sol", "gpt-6-luna"]
VARIANT = Variant("webp_720_q70", "WEBP", quality=70, method=4)


def schedule(cases, repeats, profiles=None):
    ordered = list(cases)
    random.Random(20261011).shuffle(ordered)
    for repeat in range(1, repeats + 1):
        for case in ordered if repeat % 2 else list(reversed(ordered)):
            if profiles:
                offset = ordered.index(case) % len(profiles)
                models = profiles[offset:] + profiles[:offset]
                if repeat % 2 == 0:
                    models = list(reversed(models))
            else:
                models = MODELS if (ordered.index(case) + repeat) % 2 else list(reversed(MODELS))
            for model in models:
                yield repeat, case, model


def profile_configs(report):
    return report["identity"].get("profiles") or [
        {"profile": model, "model": model, "effort": report["identity"]["effort"]}
        for model in report["identity"].get("models", MODELS)
    ]


def summarize(report):
    summaries = []
    configs = profile_configs(report)
    for config in configs:
        model = config["model"]
        rows = [r for r in report["rows"] if r.get("profile", r["model"]) == config["profile"]]
        valid = [r for r in rows if "error" not in r]
        item = {
            "model": model,
            "attempts": len(rows),
            "successful": len(valid),
            "errors": len(rows) - len(valid),
            "text_correct": sum(r["ocr_correct"] for r in valid),
            "text_evaluated": 3 * len(valid),
            "target_hits": sum(r["target_hit"] for r in valid),
            "all_checks_passed": sum(r["passed"] for r in rows),
        }
        if "profiles" in report["identity"]:
            item.update(profile=config["profile"], effort=config["effort"])
        if valid:
            for key in ("request_seconds", "total_seconds", "conversion_seconds"):
                item["median_" + key] = statistics.median(r[key] for r in valid)
            # Inclusive interpolation, consistent across both models.
            item["p90_total_seconds"] = float(
                __import__("numpy").quantile([r["total_seconds"] for r in valid], 0.9)
            )
            for key in ("input_tokens", "output_tokens"):
                usage = [r["usage"][key] for r in valid if r.get("usage") and key in r["usage"]]
                if usage:
                    item["median_" + key] = statistics.median(usage)
        summaries.append(item)
    report["summary"] = summaries
    comparisons = []
    for config in configs[1:]:
        baseline, candidate = configs[0]["profile"], config["profile"]
        analysis = analyze(
            {
                "rows": [
                    {
                        **r,
                        "variant": "png_original"
                        if r.get("profile", r["model"]) == baseline
                        else "webp_q70",
                    }
                    for r in report["rows"]
                    if r.get("profile", r["model"]) in {baseline, candidate}
                ]
            }
        )
        comparisons.append({"baseline": baseline, "candidate": candidate, **analysis})
    if "profiles" in report["identity"]:
        report["paired_comparisons"] = comparisons
    else:
        report["paired_analysis"] = {
            k: v for k, v in comparisons[0].items() if k not in {"baseline", "candidate"}
        }


async def catalog(binary, effort, profiles=None):
    client = provider._client_for_running_loop(
        binary=binary, client_name="artemis", client_title="Artemis", client_version="1.0"
    )
    await client.start()
    result = await client.request("model/list", {})
    selected = []
    configs = profiles or [{"model": model, "effort": effort} for model in MODELS]
    for config in configs:
        model, requested_effort = config["model"], config["effort"]
        matches = [
            m for m in result.get("data", []) if m.get("model") == model or m.get("id") == model
        ]
        if len(matches) != 1:
            raise RuntimeError(f"Requested model unavailable in model/list: {model}")
        entry = matches[0]
        supported = [r["reasoningEffort"] for r in entry.get("supportedReasoningEfforts", [])]
        if requested_effort not in supported:
            raise RuntimeError(
                f"Unsupported requested reasoning effort: {model}/{requested_effort}"
            )
        selected.append(
            {"model": model, "effort": requested_effort, "supported_reasoning_efforts": supported}
        )
    return selected


async def request(llm, model, case, raw, timeout):
    response = await asyncio.wait_for(llm.ainvoke(messages_for(case, raw, VARIANT)), timeout + 10)
    if response.response_metadata.get("model") != model:
        await release_response(response)
        raise RuntimeError("Response model does not match the requested model")
    return response


async def run(args):
    configs = []
    for value in args.profile or []:
        model, separator, effort = value.partition(":")
        if not separator or not model or not effort:
            raise ValueError("Each --profile must be model:effort")
        configs.append({"profile": f"{model}/{effort}", "model": model, "effort": effort})
    if not configs:
        configs = [{"profile": model, "model": model, "effort": args.effort} for model in MODELS]
    if len(configs) < 2 or len({c["profile"] for c in configs}) != len(configs):
        raise ValueError("At least two distinct model/effort profiles are required")
    args.output = args.output.resolve()
    args.manifest = args.manifest.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.manifest)
    binary = provider.find_codex_binary(args.codex_binary)
    path = args.output / "results.json"
    if path.exists() and not args.resume:
        raise ValueError("Results already exist; use --resume or a fresh directory")
    local, originals = [], {}
    for case in manifest["cases"]:
        original = (args.manifest.parent / case["file"]).read_bytes()
        originals[case["id"]] = original
        raw = prepare_image_bytes(original)
        effective, _, changed = provider._prepare_image_bytes(raw, ".webp")
        if (
            changed
            or raw != effective
            or prepare_image_messages(messages_for(case, raw, VARIANT))
            != messages_for(case, raw, VARIANT)
        ):
            raise RuntimeError("Adapter changed the prepared image; comparison invalid")
        local.append({"case": case["id"], "image": image_info(raw)})
    identity = {
        "models": list(dict.fromkeys(c["model"] for c in configs)),
        "effort": args.effort,
        "repeats": args.repeats,
        "timeout": args.timeout,
        "codex_binary": binary,
        "codex_version": subprocess.check_output([binary, "--version"], text=True).strip(),
        "manifest_sha256": digest(args.manifest.read_bytes()),
        "script_sha256": digest(Path(__file__).read_bytes()),
        "policy_sha256": digest((ROOT / "artemis/llm/image_inputs.py").read_bytes()),
        "images": local,
    }
    if args.profile:
        identity["profiles"] = configs
    report = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {
            "identity": identity,
            "manifest": manifest,
            "rows": [],
            "warmups": [],
            "started_at": datetime.now(UTC).isoformat(),
            "scope": "Fixed ARTEMIS-verified pages, rotating profile order per page, reversed in round two. "
            "Production 720p WebP Q70 policy, identical image bytes, prompts and schema. "
            "Three exact text checks plus target-in-bounds; ground truth never sent. "
            "Request includes adapter/App Server/upload/queue/inference/output; total includes PNG decode/resize/WebP encoding. "
            "Capture, source reads, report writes, warmups and cleanup excluded. No visual cache, retries, fallback or device actions.",
        }
    )
    if report["identity"] != identity:
        raise ValueError("Resume identity changed; use a fresh output directory")
    save_json(path, report)
    done = {(r["repeat"], r["case"], r.get("profile", r["model"])) for r in report["rows"]}
    try:
        report["catalog"] = await catalog(binary, args.effort, configs)
        print(json.dumps({"catalog": report["catalog"]}), flush=True)
        if args.catalog_only:
            return
        llms = {
            c["profile"]: CodexAppServerChatModel(
                model_name=c["model"],
                reasoning_effort=c["effort"],
                timeout_seconds=args.timeout,
                codex_binary=binary,
            ).bind_tools([ScreenRead], tool_choice="required")
            for c in configs
        }
        for config in configs:
            model, profile = config["model"], config["profile"]
            case = manifest["cases"][0]
            raw = prepare_image_bytes(originals[case["id"]])
            start = time.perf_counter()
            response = await request(llms[profile], model, case, raw, args.timeout)
            report["warmups"].append(
                {
                    "model": model,
                    "profile": profile,
                    "effort": config["effort"],
                    "seconds": time.perf_counter() - start,
                }
            )
            await release_response(response)
            save_json(path, report)
            print(
                json.dumps({"warmup": profile, "seconds": report["warmups"][-1]["seconds"]}),
                flush=True,
            )
        by_profile = {c["profile"]: c for c in configs}
        labels = list(by_profile) if args.profile else None
        for repeat, case, profile in schedule(manifest["cases"], args.repeats, labels):
            config = by_profile[profile]
            model = config["model"]
            if (repeat, case["id"], profile) in done:
                continue
            row = {
                "repeat": repeat,
                "case": case["id"],
                "model": model,
                "started_at": datetime.now(UTC).isoformat(),
            }
            if args.profile:
                row.update(profile=profile, effort=config["effort"])
            response = None
            start = time.perf_counter()
            raw = prepare_image_bytes(originals[case["id"]])
            converted = time.perf_counter()
            try:
                response = await request(llms[profile], model, case, raw, args.timeout)
                calls = response.tool_calls
                decision = (
                    calls[0]["args"] if len(calls) == 1 and calls[0]["name"] == "ScreenRead" else {}
                )
                row.update(
                    decision=decision,
                    usage=response.usage_metadata,
                    telemetry=response.response_metadata,
                    **score(case, decision),
                )
            except Exception as exc:
                row.update(error=type(exc).__name__, message=str(exc), **score(case, {}))
            end = time.perf_counter()
            row.update(
                conversion_seconds=converted - start,
                request_seconds=end - converted,
                total_seconds=end - start,
                submitted_image=image_info(raw),
            )
            expected = next(r["image"]["sha256"] for r in local if r["case"] == case["id"])
            if row["submitted_image"]["sha256"] != expected:
                raise RuntimeError("Submitted payload differs from preflight input")
            report["rows"].append(row)
            summarize(report)
            save_json(path, report)
            print(
                json.dumps(
                    {
                        k: row[k]
                        for k in (
                            "repeat",
                            "case",
                            "model",
                            "profile",
                            "effort",
                            "request_seconds",
                            "total_seconds",
                            "ocr_correct",
                            "target_hit",
                            "passed",
                        )
                        if k in row
                    }
                ),
                flush=True,
            )
            if response is not None:
                row["cleanup"] = await release_response(response)
                save_json(path, report)
            if "error" in row:
                raise RuntimeError(
                    "Inference failed; preserve attempt and diagnose before continuing"
                )
        report["completed_at"] = datetime.now(UTC).isoformat()
    except Exception as exc:
        report.setdefault("run_errors", []).append(
            {"error": type(exc).__name__, "message": str(exc)}
        )
        raise
    finally:
        summarize(report)
        save_json(path, report)
        await close_clients()
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codex-binary")
    parser.add_argument("--effort", default="medium")
    parser.add_argument(
        "--profile", action="append", help="Repeat model:effort; first is the paired baseline"
    )
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--catalog-only", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or args.timeout <= 0:
        parser.error("Positive repetitions and timeout required")
    asyncio.run(run(args))
