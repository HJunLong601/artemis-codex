"""Publish a deidentified model/effort comparison with a same-run baseline."""

import argparse
from html import escape
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.benchmark_models import profile_configs, summarize
from scripts.report_png_webp import table
import matplotlib.pyplot as plt
import numpy as np


def samples_for(rows, profile):
    return [r for r in rows if r["profile"] == profile]


def aggregate(samples):
    valid = [r for r in samples if "error" not in r]
    known = [r for r in valid if r.get("telemetry", {}).get("cache_usage_available")]
    return {
        "attempts": len(samples),
        "successful": len(valid),
        "errors": len(samples) - len(valid),
        "text_correct": sum(r["ocr_correct"] for r in valid),
        "text_evaluated": 3 * len(valid),
        "target_hits": sum(r["target_hit"] for r in valid),
        "all_checks_passed": sum(r["passed"] for r in samples),
        "median_request_seconds": statistics.median(r["request_seconds"] for r in valid)
        if valid
        else None,
        "median_total_seconds": statistics.median(r["total_seconds"] for r in valid)
        if valid
        else None,
        "p90_total_seconds": float(np.quantile([r["total_seconds"] for r in valid], 0.9))
        if valid
        else None,
        "median_conversion_seconds": statistics.median(r["conversion_seconds"] for r in valid)
        if valid
        else None,
        "requests_with_cache_telemetry": len(known),
        "requests_with_cached_input": sum(
            r.get("usage", {}).get("input_token_details", {}).get("cache_read", 0) > 0
            for r in known
        ),
        "median_cache_read_tokens": statistics.median(
            r.get("usage", {}).get("input_token_details", {}).get("cache_read", 0) for r in known
        )
        if known
        else None,
    }


def public_data(report):
    configs = profile_configs(report)
    cases = report["manifest"]["cases"]
    rounds = report["identity"]["repeats"]
    rows = report["rows"]
    expected = {
        (n, c["id"], p["profile"]) for n in range(1, rounds + 1) for c in cases for p in configs
    }
    actual = [(r["repeat"], r["case"], r["profile"]) for r in rows]
    if not report.get("completed_at") or len(actual) != len(expected) or set(actual) != expected:
        raise ValueError("Refusing incomplete or duplicate model/effort comparison")
    summarize(report)
    image_sizes = sorted(
        {(i["image"]["width"], i["image"]["height"]) for i in report["identity"]["images"]}
    )
    summaries = [{**c, **aggregate(samples_for(rows, c["profile"]))} for c in configs]
    per_page = [
        {
            "index": int(c["id"]),
            "page": c["page"],
            "profiles": [
                {
                    **p,
                    **aggregate(
                        [r for r in rows if r["case"] == c["id"] and r["profile"] == p["profile"]]
                    ),
                }
                for p in configs
            ],
        }
        for c in cases
    ]
    per_round = [
        {
            "round": n,
            "profiles": [
                {
                    **p,
                    **aggregate(
                        [r for r in rows if r["repeat"] == n and r["profile"] == p["profile"]]
                    ),
                }
                for p in configs
            ],
        }
        for n in range(1, rounds + 1)
    ]
    comparisons = [
        {**{k: v for k, v in a.items() if k != "pairs"}, "paired_samples": len(a["pairs"])}
        for a in report["paired_comparisons"]
    ]
    return {
        "date": "2026-10-11",
        "profiles": configs,
        "pages": len(cases),
        "rounds": rounds,
        "baseline": configs[0]["profile"],
        "input": {
            "format": "WEBP",
            "dimensions": image_sizes,
            "quality": 70,
            "method": 4,
            "lossless": False,
            "total_bytes": sum(i["image"]["bytes"] for i in report["identity"]["images"]),
        },
        "summary": summaries,
        "per_page": per_page,
        "per_round": per_round,
        "paired_comparisons": comparisons,
        "method": report["scope"],
        "limitations": "Small fixed-screen sample; text-region/target checks are not general UI-action success. Provider prompt caching not controlled; per-profile cache counters reported. Serial rotating order reversed in round two. Same-run medium baseline, no pooling with historical timings. Includes client, queue and network overhead; not isolated model compute. Requested effort verified against model/list and set on the production adapter; response metadata reports model but not effective effort. No local location cache, retries, fallback or device actions.",
        "privacy": "Aggregate numeric metrics and generic page names only; no screenshots, device/account identifiers, raw responses, session IDs or local paths.",
        "model_references": [
            "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
            "https://developers.openai.com/api/docs/models/gpt-6-astra",
        ],
    }


def number(value):
    return "—" if value is None else f"{value:.2f}"


def render(path, public_path):
    report = json.loads(path.read_text(encoding="utf-8"))
    public = public_data(report)
    out = path.parent
    public_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(public, ensure_ascii=False, indent=2) + "\n"
    public_path.write_text(encoded, encoding="utf-8")
    (out / "summary-public.json").write_text(encoded, encoding="utf-8")
    configs, summaries, pages = public["profiles"], public["summary"], public["per_page"]
    labels = [c["profile"].replace("/", " · ") for c in configs]
    figure, ax = plt.subplots(figsize=(12, 4.8), constrained_layout=True)
    x = np.arange(len(pages))
    width = 0.8 / len(configs)
    for i, label in enumerate(labels):
        times = [p["profiles"][i]["median_total_seconds"] for p in pages]
        ax.bar(x + (i - (len(configs) - 1) / 2) * width, times, width, label=label)
    ax.set_xticks(x, [f"Page {p['index']:02}" for p in pages])
    ax.set_ylabel("Median total latency (seconds)")
    ax.set_title(f"Same screenshots, WebP Q70 720p, {public['rounds']} rounds")
    ax.legend(ncols=2)
    ax.grid(axis="y", alpha=0.2)
    figure.savefig(out / "latency.png", dpi=160)
    plt.close(figure)
    summary_rows = [
        [
            s["model"],
            s["effort"],
            number(s["median_request_seconds"]),
            number(s["median_total_seconds"]),
            number(s["p90_total_seconds"]),
            f"{s['text_correct']}/{s['text_evaluated']}",
            f"{s['target_hits']}/{s['successful']}",
            f"{s['all_checks_passed']}/{s['attempts']}",
            s["errors"],
        ]
        for s in summaries
    ]
    paired_rows = [
        [
            a["candidate"],
            f"{a['geomean_ratio']:.3f}",
            f"{a['ratio_ci95'][0]:.3f}–{a['ratio_ci95'][1]:.3f}",
            f"{a['faster_pairs']}/{a['paired_samples']}",
            "本次更快"
            if a["ratio_ci95"][1] < 1
            else "本次更慢"
            if a["ratio_ci95"][0] > 1
            else "区间跨 1，差异未确认",
        ]
        for a in public["paired_comparisons"]
    ]
    page_rows = [
        [
            p["page"],
            *[
                f"{number(s['median_total_seconds'])} · {s['text_correct']}/{s['text_evaluated']} · {s['target_hits']}/{s['attempts']}"
                for s in p["profiles"]
            ],
        ]
        for p in pages
    ]
    round_rows = [
        [
            n["round"],
            *[
                f"{number(s['median_total_seconds'])} · {s['all_checks_passed']}/{s['attempts']}"
                for s in n["profiles"]
            ],
        ]
        for n in public["per_round"]
    ]
    cache_rows = [
        [
            s["profile"],
            f"{s['requests_with_cached_input']}/{s['requests_with_cache_telemetry']}",
            s["median_cache_read_tokens"],
        ]
        for s in summaries
    ]
    names = {c["id"]: c["page"] for c in report["manifest"]["cases"]}
    failed = [
        [
            names[r["case"]],
            r["repeat"],
            r["profile"],
            r["ocr_correct"],
            r["target_hit"],
            r.get("error", "识别核对未通过"),
        ]
        for r in report["rows"]
        if not r["passed"]
    ]
    failure_html = (
        table(["页面", "轮次", "配置", "文字通过数", "目标命中", "类型"], failed)
        if failed
        else "<p>所有请求均通过三项文字及目标位置核对。</p>"
    )
    baseline = escape(public["baseline"])
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Sol 推理档位 / Astra 图片识别实测</title>
<style>body{{font:16px/1.65 system-ui,sans-serif;background:#f5f7fb;color:#17243d;max-width:1280px;margin:40px auto;padding:0 24px}}h1,h2{{line-height:1.3}}section{{background:white;padding:24px;margin:20px 0;border-radius:16px}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #e5e9f0}}th{{background:#edf2fb}}.scroll{{overflow:auto}}img{{width:100%}}.note{{color:#536079}}code{{background:#edf2fb;padding:2px 5px}}</style>
<h1>GPT-6.1 Sol 推理档位 / GPT-6 Astra 图片识别实测</h1><p class="note">{public["date"]} · {public["pages"]} 个实机验证页面 · 每组 {public["pages"] * public["rounds"]} 次正式测量 · 720×1600 WebP Q70</p>
<section><h2>同轮对照结果</h2>{table(["模型", "推理", "请求中位数 s", "总耗时中位数 s", "总耗时 P90 s", "文字核对", "目标命中", "全部核对通过", "调用错误"], summary_rows)}<p>文字核对使用指定区域的文字精确匹配；目标命中检查返回点是否位于实机确认的边界内。全部通过要求三个文字区域与目标点同时正确。识别失败仍计入耗时及通过率。</p></section>
<section><h2>与本轮基线 {baseline} 配对比较</h2>{table(["候选配置", "候选 / 基线耗时比", "95% 页面聚类区间", "更快的配对", "判断"], paired_rows)}<p>按同一页面、同一轮次配对，计算总耗时比的几何均值；以页面为聚类单位 bootstrap 10000 次，区间未校正多重比较。比值小于 1 表示候选更快。没有使用之前时段的 Sol medium 或 Luna 测量。</p></section>
<section><h2>逐页对照</h2><img src="latency.png" alt="四组配置逐页耗时对照">{table(["页面", *labels], page_rows)}<p>单元格为总耗时中位数（秒） · 文字通过数 · 目标命中数。</p></section>
<section><h2>逐轮结果</h2>{table(["轮次", *labels], round_rows)}<p>单元格为总耗时中位数（秒） · 全部核对通过数。</p></section>
<section><h2>核对未通过的请求</h2>{failure_html}</section>
<section><h2>缓存遥测与测量范围</h2>{table(["配置", "提示缓存命中 / 有遥测请求", "缓存读取 token 中位数"], cache_rows)}
<p>相同原始 PNG、生产图片转换策略、图片字节、提示词及输出结构。每页轮换配置先后位置，第二轮反转；串行运行，各组预热一次，预热不计入统计。未发送文字答案或目标边界。模型及推理档位先经 model/list 检查，再显式传给适配器；回复遥测核对模型名称，不返回实际生效的推理档位。</p>
<p>请求计时包含适配器、App Server、上传、排队、推理和输出；总耗时另含 PNG 解码、缩放及 WebP 编码。截图采集、源文件读取、报告写入和会话清理不计时。没有本地视觉定位缓存、重试、模型回退或设备动作；各临时会话在计时结束后释放。</p>
<p>服务端提示缓存未控制冷/热状态，命中差异可能影响耗时；结果反映实际调用链路，不能单独归因于模型计算速度。有限的简单固定页面不能代表复杂页面、缺失目标、动态操作或整条自动化任务的成功率。</p>
<p>输入总计 {public["input"]["total_bytes"] / 1024:.1f} KiB。公开报告不含截图、设备/账号标识、原始回复或会话标识。<a href="summary-public.json">脱敏数据</a> · <a href="{public["model_references"][0]}">官方 Sol 说明</a> · <a href="{public["model_references"][1]}">官方 Astra 说明</a></p></section></html>'''
    (out / "report.html").write_text(html, encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(out / "report.html"),
                "summary": summaries,
                "paired": public["paired_comparisons"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument(
        "--public", type=Path, default=ROOT / "docs/benchmarks/sol-efforts-astra-2026-10-11.json"
    )
    args = parser.parse_args()
    render(args.results, args.public)
