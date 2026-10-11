"""Publish aggregate model-comparison metrics and a local HTML report."""

import argparse
from html import escape
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_models import MODELS, summarize
from scripts.report_png_webp import table
import matplotlib.pyplot as plt
import numpy as np


def render(path, public_path):
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = report["rows"]
    cases = report["manifest"]["cases"]
    expected = len(cases) * len(MODELS) * report["identity"]["repeats"]
    if not report.get("completed_at") or len(rows) != expected:
        raise ValueError("Refusing to publish incomplete comparison")
    summarize(report)
    analysis = report["paired_analysis"]
    summaries = []
    for s in report["summary"]:
        # Allowlist aggregate values; never publish raw responses or session metadata.
        values = {k: v for k, v in s.items() if k != "cache_reads_observed"}
        samples = [r for r in rows if r["model"] == s["model"] and "error" not in r]
        known = [r for r in samples if r.get("telemetry", {}).get("cache_usage_available")]
        values["requests_with_cache_telemetry"] = len(known)
        values["requests_with_cached_input"] = sum(
            r.get("usage", {}).get("input_token_details", {}).get("cache_read", 0) > 0
            for r in known
        )
        values["median_cache_read_tokens"] = (
            statistics.median(
                r.get("usage", {}).get("input_token_details", {}).get("cache_read", 0)
                for r in known
            )
            if known
            else None
        )
        summaries.append(values)
    per_page = []
    for case in cases:
        entry = {"page": case["page"], "index": int(case["id"]), "models": []}
        for model in MODELS:
            samples = [r for r in rows if r["case"] == case["id"] and r["model"] == model]
            valid = [r for r in samples if "error" not in r]
            entry["models"].append(
                {
                    "model": model,
                    "median_total_seconds": statistics.median(r["total_seconds"] for r in valid)
                    if valid
                    else None,
                    "text_correct": sum(r["ocr_correct"] for r in valid),
                    "text_evaluated": 3 * len(valid),
                    "target_hits": sum(r["target_hit"] for r in valid),
                    "passed": sum(r["passed"] for r in samples),
                    "attempts": len(samples),
                }
            )
        per_page.append(entry)
    public = {
        "date": "2026-10-11",
        "models": MODELS,
        "reasoning_effort": report["identity"]["effort"],
        "pages": len(cases),
        "rounds": report["identity"]["repeats"],
        "input": {
            "format": "WEBP",
            "dimensions": [720, 1600],
            "quality": 70,
            "method": 4,
            "lossless": False,
            "total_bytes_8_images": sum(i["image"]["bytes"] for i in report["identity"]["images"]),
        },
        "summary": summaries,
        "per_page": per_page,
        "paired_total_time": {k: v for k, v in analysis.items() if k != "pairs"},
        "paired_samples": len(analysis["pairs"]),
        "method": report["scope"],
        "limitations": "Small fixed-screen sample; recognition checks are not general UI-action success or difficult-screen coverage. Provider prompt-cache hits are unequal between models; cold/warm cache was not controlled. Local visual-location cache bypassed. Timing includes queue/network/client overhead, not isolated model compute. No comparison against separately measured historical timings.",
        "privacy": "Allowlisted aggregate only: no screenshots, device/account identifiers, raw responses, session IDs or local absolute paths.",
        "model_reference": "https://developers.openai.com/api/docs/models/gpt-6-luna",
    }
    public_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.write_text(
        json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    out = path.parent
    (out / "summary-public.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    figure, ax = plt.subplots(figsize=(11, 4.5), constrained_layout=True)
    x = np.arange(len(cases))
    for index, model in enumerate(MODELS):
        medians = [p["models"][index]["median_total_seconds"] for p in per_page]
        ax.bar(x + (index - 0.5) * 0.36, medians, 0.36, label=model)
    ax.set_xticks(x, [f"Page {p['index']:02}" for p in per_page])
    ax.set_ylabel("Median total latency (seconds)")
    ax.set_title("Same screenshots, WebP Q70 720p, medium reasoning, 2 rounds")
    ax.legend()
    ax.grid(axis="y", alpha=0.2)
    figure.savefig(out / "latency.png", dpi=160)
    plt.close(figure)
    summary_rows = [
        [
            s["model"],
            f"{s['median_request_seconds']:.2f}",
            f"{s['median_total_seconds']:.2f}",
            f"{s['p90_total_seconds']:.2f}",
            f"{s['text_correct']}/{s['text_evaluated']}",
            f"{s['target_hits']}/{s['successful']}",
            f"{s['all_checks_passed']}/{s['attempts']}",
            s["errors"],
        ]
        for s in summaries
    ]
    page_rows = [
        [
            p["page"],
            *[f"{m['median_total_seconds']:.2f}" for m in p["models"]],
            *[
                f"{m['text_correct']}/{m['text_evaluated']} · {m['target_hits']}/{m['attempts']}"
                for m in p["models"]
            ],
        ]
        for p in per_page
    ]
    lo, hi = analysis["ratio_ci95"]
    result = (
        "配对区间支持本次样本中 Luna 更快。"
        if hi < 1
        else "配对区间支持本次样本中 Luna 更慢。"
        if lo > 1
        else "区间跨过 1，尚不能确认稳定的速度差异。"
    )
    failures = [
        [
            next(c["page"] for c in cases if c["id"] == r["case"]),
            r["repeat"],
            r["model"],
            r["ocr_correct"],
            r["target_hit"],
            r.get("error", "识别核对未通过"),
        ]
        for r in rows
        if not r["passed"]
    ]
    failure_html = (
        table(["页面", "轮次", "模型", "文字通过数", "目标命中", "类型"], failures)
        if failures
        else "<p>本次所有请求均通过三项文字及目标位置核对。</p>"
    )
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Luna / Sol 图片识别实测</title>
<style>body{{font:16px/1.65 system-ui,sans-serif;background:#f5f7fb;color:#17243d;max-width:1140px;margin:40px auto;padding:0 24px}}h1,h2{{line-height:1.3}}section{{background:white;padding:24px;margin:20px 0;border-radius:16px}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #e5e9f0}}th{{background:#edf2fb}}.scroll{{overflow:auto}}img{{width:100%}}.note{{color:#536079}}code{{background:#edf2fb;padding:2px 5px}}</style>
<h1>GPT-6 Luna / GPT-6.1 Sol 图片识别实测</h1><p class="note">2026-10-11 · 8 个实机验证页面 · 每模型 16 次测量 · medium · 720×1600 WebP Q70</p>
<section><h2>同轮对照结果</h2>{table(["模型", "请求中位数 s", "总耗时中位数 s", "总耗时 P90 s", "文字核对", "目标命中", "全部核对通过", "错误"], summary_rows)}
<p>配对总耗时比 Luna / Sol：<strong>{analysis["geomean_ratio"]:.3f}</strong>，95% 页面聚类 bootstrap 区间 <strong>{lo:.3f}–{hi:.3f}</strong>，{len(analysis["pairs"])} 对。{result}</p></section>
<section><h2>逐页耗时与识别</h2><img src="latency.png" alt="逐页模型总耗时中位数">{table(["页面", "Sol 中位数 s", "Luna 中位数 s", "Sol 文字 · 目标", "Luna 文字 · 目标"], page_rows)}</section>
<section><h2>核对未通过的请求</h2>{failure_html}</section>
<section><h2>测量方法与限制</h2><p>相同原始 PNG、默认转换策略、图片字节、提示词及结构化输出；每页两轮，模型先后顺序反转，串行配对运行。每模型预热一次，预热不计入结果。答案和核对用目标边界没有发送给模型。没有重试、模型回退、设备动作或视觉定位缓存。</p>
<p>请求计时包含适配器、App Server、上传、排队、推理和输出；总耗时另包含 PNG 解码、缩放和 WebP 编码。截图采集、源文件读取、报告写入及会话清理不计时。每个完成的临时会话在计时结束后释放。</p>
<p>重复输入可能受 Provider 的提示缓存影响。模型缓存命中请求：Sol {summaries[0]["requests_with_cached_input"]}/{summaries[0]["requests_with_cache_telemetry"]}；Luna {summaries[1]["requests_with_cached_input"]}/{summaries[1]["requests_with_cache_telemetry"]}（分母为有缓存遥测的请求）。</p>
<p>两模型缓存命中不均衡，未控制冷缓存或热缓存；结果反映当前调用链路的实际请求耗时，不能单独归因于模型计算速度。</p>
<p>8 页简单界面的有限核对，不代表复杂页面或整条自动化任务的成功率；这次数据不与历史分时测试直接合并。识别失败仍计入耗时与全部核对通过率。</p>
<p>8 张输入总计 {public["input"]["total_bytes_8_images"] / 1024:.1f} KiB。报告没有截图、设备/账号标识或模型完整回复。<a href="summary-public.json">脱敏数据</a> · <a href="{public["model_reference"]}">官方 Luna 模型说明</a></p></section></html>'''
    (out / "report.html").write_text(html, encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(out / "report.html"),
                "summary": summaries,
                "paired": public["paired_total_time"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument(
        "--public", type=Path, default=ROOT / "docs/benchmarks/models-luna-sol-2026-10-11.json"
    )
    args = parser.parse_args()
    render(args.results, args.public)
