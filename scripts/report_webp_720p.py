#!/usr/bin/env python3
"""Render native-versus-720p WebP Q70 paired results, without mixing old timings."""

from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.report_png_webp import analyze, table
import matplotlib.pyplot as plt
import numpy as np


def render(path):
    path = path.resolve()
    out = path.parent
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = report["rows"]
    cases = report["manifest"]["cases"]
    variants = ["webp_native_q70", "webp_720_q70"]
    if (
        not report.get("completed_at")
        or len(rows) != len(cases) * 2 * report["identity"]["repeats"]
    ):
        raise ValueError("Refusing to publish incomplete model results")
    # Reuse the exact paired ratio and page-cluster bootstrap from the previous report.
    mapped = [
        {**r, "variant": "png_original" if r["variant"] == variants[0] else "webp_q70"}
        for r in rows
    ]
    analysis = analyze({"rows": mapped})
    (out / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {s["variant"]: s for s in report["summary"]}
    native, small = [summary[v] for v in variants]
    reduction = 1 - small["total_bytes"] / native["total_bytes"]
    png_total = sum((out.parent / case["file"]).stat().st_size for case in cases)
    png_reduction = 1 - small["total_bytes"] / png_total
    lo, hi = analysis["ratio_ci95"]
    conclusion = (
        "这轮配对结果支持 720p 总耗时更低，仍需更多时段验证。"
        if hi < 1
        else "这轮配对结果显示 720p 总耗时更高。"
        if lo > 1
        else "配对耗时比的 95% 区间跨过 1，尚不能确认稳定提速。"
    )
    summary_rows = []
    for label, s in [("原尺寸 WebP Q70", native), ("720p WebP Q70", small)]:
        summary_rows.append(
            [
                label,
                f"{s['total_bytes'] / 1024:.1f}",
                f"{s['median_kib']:.1f}",
                f"{s['median_conversion_ms']:.1f}",
                f"{s['median_request_seconds']:.2f}",
                f"{s['median_total_seconds']:.2f}",
                f"{s['ocr_correct']}/{s['ocr_total']}",
                f"{s['target_hits']}/{s['successful']}",
                f"{s['passed']}/{s['attempts']}",
                s["errors"],
                s.get("median_input_tokens", "未知"),
            ]
        )
    per_page, chart = [], []
    failures = []
    for case in cases:
        local = {
            v: next(x for x in report["local"] if x["case"] == case["id"] and x["variant"] == v)
            for v in variants
        }
        times = {
            v: statistics.median(
                r["total_seconds"]
                for r in rows
                if r["case"] == case["id"] and r["variant"] == v and "error" not in r
            )
            for v in variants
        }
        n, s = [local[v] for v in variants]
        per_page.append(
            [
                case["page"],
                f"{n['image']['bytes'] / 1024:.1f}",
                f"{s['image']['bytes'] / 1024:.1f}",
                f"{s['conversion_ms']:.1f}",
                f"{times[variants[0]]:.2f}",
                f"{times[variants[1]]:.2f}",
                f"{times[variants[1]] - times[variants[0]]:+.2f}",
                f"{s['image']['width']}×{s['image']['height']}",
                s["default_adapter_changed"],
            ]
        )
        chart.append(
            (
                case["page"],
                n["image"]["bytes"] / 1024,
                s["image"]["bytes"] / 1024,
                times[variants[0]],
                times[variants[1]],
            )
        )
    for row in rows:
        if not row["passed"]:
            failures.append(
                [
                    row["case"],
                    row["repeat"],
                    row["variant"],
                    json.dumps(row.get("decision"), ensure_ascii=False),
                    row["ocr_matches"],
                    row["target_hit"],
                    row.get("message", ""),
                ]
            )
    plt.rcParams.update(
        {
            "font.family": "Microsoft YaHei",
            "axes.unicode_minus": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "font.size": 10,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.7))
    y = np.arange(len(cases))
    for ax, offset, title in [
        (axes[0], 1, "文件大小（KiB）"),
        (axes[1], 3, "解码、缩放、编码 + 模型请求中位耗时（秒）"),
    ]:
        for delta, col, label, color in [
            (-0.18, offset, "原尺寸 WebP Q70", "#4875c7"),
            (0.18, offset + 1, "720p WebP Q70", "#24a08d"),
        ]:
            values = [x[col] for x in chart]
            ax.barh(y + delta, values, height=0.32, label=label, color=color)
            for i, value in enumerate(values):
                ax.text(
                    value + max(values) * 0.015, i + delta, f"{value:.1f}", va="center", fontsize=8
                )
        ax.set_yticks(y, [x[0] for x in chart] if offset == 1 else [""] * len(cases))
        ax.invert_yaxis()
        ax.set_xlim(0, max(max(x[offset], x[offset + 1]) for x in chart) * 1.2)
        ax.set_title(title, pad=16)
        ax.grid(axis="x", alpha=0.15)
        ax.set_axisbelow(True)
    fig.suptitle("WebP 有损 Q70：1080×2400 → 720×1600 · gpt-6.1-sol / medium", fontsize=15)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.53, 0.94), ncol=2)
    fig.text(
        0.02,
        0.02,
        f"{len(cases)} 页 × 2 分辨率 × {report['identity']['repeats']} 轮。请求含上传和模型响应；会话清理不计时。",
        fontsize=10,
        color="#566779",
    )
    fig.tight_layout(rect=(0, 0.055, 1, 0.95))
    fig.savefig(out / "comparison.png", dpi=170, facecolor="white")
    plt.close(fig)
    gallery = "".join(
        f'<figure><a href="inputs/{escape(c["id"])}/webp_720_q70.webp"><img src="inputs/{escape(c["id"])}/webp_720_q70.webp" loading="lazy"></a><figcaption>{escape(c["page"])} · <a href="inputs/{escape(c["id"])}/webp_native_q70.webp">原尺寸</a> / <a href="inputs/{escape(c["id"])}/webp_720_q70.webp">720p</a></figcaption></figure>'
        for c in cases
    )
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>720p WebP Q70 基准</title><style>
body{{font:16px/1.75 "Microsoft YaHei",sans-serif;background:#f1f4f9;color:#20344a;margin:0}}main{{max-width:1250px;padding:40px;margin:30px auto;background:white;border-radius:16px}}h1{{font-size:30px}}h2{{margin-top:36px;border-top:1px solid #e1e7ef;padding-top:24px}}.lead{{background:#eaf4f7;padding:18px 24px;border-left:4px solid #24a08d}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #e3e8ef;text-align:left}}th{{background:#edf2f8;white-space:nowrap}}.scroll{{overflow:auto}}.chart{{width:100%}}.gallery{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}figure{{margin:0}}figure img{{width:100%;border:1px solid #e0e6ee;border-radius:6px}}figcaption{{font-size:13px}}a{{color:#295bbb}}pre{{overflow:auto;padding:16px;background:#eef2f6}}@media(max-width:700px){{main{{margin:0;padding:20px}}.gallery{{grid-template-columns:repeat(2,1fr)}}}}</style><main>
<p>2026-10-10 · 8 个 ARTEMIS 核验页面 · gpt-6.1-sol / medium</p><h1>缩至 720p，再编码 WebP 有损 Q70</h1>
<div class="lead"><p>同一批截图，从 1080×2400 WebP 改为 720×1600 WebP，8 图合计体积减少 <strong>{reduction * 100:.1f}%</strong>，像素总数减少 55.6%。720p 路径解码、缩放与编码中位耗时 <strong>{small["median_conversion_ms"]:.0f} ms</strong>。</p><p>本轮同时段总耗时中位数：原尺寸 <strong>{native["median_total_seconds"]:.2f}s</strong>，720p <strong>{small["median_total_seconds"]:.2f}s</strong>。{conclusion}</p></div>
<p>两种分辨率都从原始 PNG 开始，统一使用 Pillow Lanczos 缩放、quality=70、method=4、lossless=False；720p 指竖屏宽 720 像素，保持比例，得到 720×1600。质量参数 70 不代表固定压缩率。原尺寸对照在本轮重新调用，旧报告的时间未混入。相对原始 PNG 的 {png_total / 1024:.1f} KiB，720p WebP 体积减少 {png_reduction * 100:.1f}%。</p>
<img class="chart" src="comparison.png" alt="原尺寸与720p的大小和耗时">
<h2>汇总</h2>{table(["输入", "8图合计 KiB", "体积中位 KiB", "处理中位 ms", "请求中位 s", "总耗时中位 s", "OCR正确（成功调用）", "定位命中", "全通过/尝试", "调用错误", "输入token中位"], summary_rows)}
<p>两条路径先把原始 PNG 读入内存，再开始计时。处理包含 PNG 解码、RGB 转换、缩放（仅 720p）和 WebP 编码；请求包含 base64、项目适配器、App Server 会话创建、上传、排队、推理和输出。结果写盘、会话释放、设备截图与导航未计入；没有单独的上传计时。预热请求排除，没有自动重试或换模型。</p>
<h2>逐页结果</h2>{table(["页面", "原尺寸 KiB", "720p KiB", "720p处理 ms", "原尺寸总 s", "720p总 s", "差值 s", "720p实际尺寸", "默认适配器改写"], per_page)}
<p>每页每格式两次请求，第一轮页面间交替 AB/BA，第二轮反转。表内时间为成功请求中位数；本地处理列为预热后的 10 次中位数。归一化 OCR 区域与目标位置保持一致，返回点按原始截图尺寸换算后核验。</p>
<h2>配对统计</h2><p>{len(analysis["pairs"])} 个有效同页同轮配对中，720p 更快的有 {analysis["faster_pairs"]} 个。720p/原尺寸总耗时几何平均比 <strong>{analysis["geomean_ratio"]:.3f}</strong>，按页面聚类 bootstrap 10,000 次的 95% 区间 <strong>[{lo:.3f}, {hi:.3f}]</strong>。{conclusion} 仅覆盖本次时段与这 8 页，缓存、网络、服务负载和生成长度未被固定。</p>
<h2>识别质量与适配器</h2><p>每页读取 3 个已核验文字区域，并定位 1 个目标；提示词没有 OCR 答案或目标框。OCR 仅统一 NFKC 和空白后精确匹配，定位点须落在原始像素目标框内。固定图片测试不等于完整导航成功率。</p>
{table(["页面ID", "轮次", "格式", "实际输出", "OCR匹配", "定位命中", "调用错误"], failures) if failures else "<p>所有计时请求的 OCR 与定位均通过。</p>"}
<p>720×1600 WebP 同时低于默认适配器的最长边 1600 与文件大小 768 KiB 限制，实测输出字节完全不变，因此这组 720p 图片可以通过现有默认适配器保留 WebP。原尺寸对照仍使用独立进程的限制覆盖以保留原格式；生产配置未改。</p>
<p>每次响应返回后，调用 thread/unsubscribe 释放本测试创建的临时会话，操作在计时区间外。上一轮保留会话时出现 MCP 初始化资源压力，本轮记录清理方式，并同时重新测量两种分辨率，避免将会话生命周期变化和分辨率变化混为一谈。前置试运行出现一次受限环境预热超时，以及一次临时会话不支持 thread/archive 的清理错误；均发生在正式计时样本之前，记录保留在 <a href="../webp720-gpt61/results.json">试运行数据</a>。</p>
<div class="gallery">{gallery}</div>
<h2>原始数据与复现</h2><p><a href="results.json">逐次结果、图像哈希、用量与回包</a> · <a href="analysis.json">配对统计</a> · <a href="../manifest.json">页面标注</a> · <a href="../png-webp70-gpt61/report.html">上一轮 PNG/原尺寸 WebP 报告</a></p>
<p>客户端：{escape(report["identity"]["codex_version"])}；Pillow：{escape(report["identity"]["pillow"])}。同一模型与推理程度，未修改设备状态。</p>
<pre>.venv/Scripts/python.exe scripts/benchmark_webp_720p.py --manifest artifacts/image-benchmark/2026-10-10/manifest.json --output artifacts/image-benchmark/2026-10-10/{out.name} --codex-binary "{escape(report["identity"]["codex_binary"])}"
.venv/Scripts/python.exe scripts/report_webp_720p.py artifacts/image-benchmark/2026-10-10/{out.name}/results.json</pre></main></html>'''
    (out / "report.html").write_text(html, encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(out / "report.html"),
                "summary": report["summary"],
                "analysis": {k: v for k, v in analysis.items() if k != "pairs"},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("results", type=Path)
    render(p.parse_args().results)
