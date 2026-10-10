#!/usr/bin/env python3
"""Generate a paired native PNG/WebP benchmark report and scientific figure."""

from __future__ import annotations

import argparse
from collections import defaultdict
from html import escape
import json
import math
from pathlib import Path
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def table(headers, rows):
    return (
        '<div class="scroll"><table><thead><tr>'
        + "".join(f"<th>{escape(h)}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(f"<td>{escape(str(v))}</td>" for v in row) + "</tr>" for row in rows
        )
        + "</tbody></table></div>"
    )


def analyze(report):
    rows = report["rows"]
    png = {
        (r["case"], r["repeat"]): r
        for r in rows
        if r["variant"] == "png_original" and "error" not in r
    }
    pairs = []
    logs = defaultdict(list)
    for row in rows:
        key = row["case"], row["repeat"]
        if row["variant"] != "webp_q70" or "error" in row or key not in png:
            continue
        ratio = row["total_seconds"] / png[key]["total_seconds"]
        logs[row["case"]].append(math.log(ratio))
        pairs.append(
            {
                "case": row["case"],
                "repeat": row["repeat"],
                "ratio": ratio,
                "delta_seconds": row["total_seconds"] - png[key]["total_seconds"],
            }
        )
    means = np.array([statistics.mean(x) for x in logs.values()])
    if not len(means):
        return {"pairs": pairs}
    rng = np.random.default_rng(20261010)
    boot = np.exp(rng.choice(means, size=(10000, len(means)), replace=True).mean(axis=1))
    return {
        "pairs": pairs,
        "geomean_ratio": float(np.exp(means.mean())),
        "ratio_ci95": np.quantile(boot, [0.025, 0.975]).tolist(),
        "faster_pairs": sum(p["ratio"] < 1 for p in pairs),
        "pages": len(means),
    }


def render(path):
    path = path.resolve()
    out = path.parent
    report = json.loads(path.read_text(encoding="utf-8"))
    cases = report["manifest"]["cases"]
    rows = report["rows"]
    if len(rows) != len(cases) * 2 * report["identity"]["repeats"] or not report.get(
        "completed_at"
    ):
        raise ValueError("Refusing to publish an incomplete comparison")
    analysis = analyze(report)
    (out / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summaries = {r["variant"]: r for r in report["summary"]}
    png = summaries["png_original"]
    webp = summaries["webp_q70"]
    saved = 1 - webp["total_bytes"] / png["total_bytes"]
    median_delta = webp["median_total_seconds"] - png["median_total_seconds"]
    recovery_html = ""
    recovery_path = out.parent / "png-webp70-recovery" / "results.json"
    if recovery_path.exists():
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        if recovery.get("completed_at"):
            recovery_rows = []
            names = {c["id"]: c["page"] for c in cases}
            for row in recovery["rows"]:
                recovery_rows.append(
                    [
                        names[row["case"]],
                        row["variant"],
                        f"{row['request_seconds']:.2f}",
                        f"{row['total_seconds']:.2f}",
                        row["passed"],
                        row.get("message", ""),
                    ]
                )
            recovery_html = (
                "<h2>会话初始化故障与独立补测</h2><p>原实验最后 3 次请求在模型推理前失败，错误为 required MCP servers failed to initialize: artemis。同期系统可用提交内存约 37 MiB，基准 App Server 关闭后恢复至约 10 GiB；这些现象与逐线程启动 MCP 所产生的资源压力一致。诊断随后返回 ready。</p><p>在新进程中对受影响的通知页和电池页各补测一整对 PNG/WebP。补测保留相同模型、推理程度和图像参数，另做预热。下面的 4 次结果独立列出，没有替换原始失败，也没有混入上方中位数或配对置信区间：</p>"
                + table(["页面", "格式", "请求 s", "总耗时 s", "识别全通过", "错误"], recovery_rows)
                + '<p><a href="../png-webp70-recovery/results.json">补测原始数据</a>；复现时使用 <code>../recovery-manifest.json</code> 和 <code>--repeats 1</code>。后续应单独处理纯模型请求重复初始化 MCP 的开销，再在不同时间段扩大速度样本。</p>'
            )
    lo, hi = analysis["ratio_ci95"]
    speed_text = (
        "这次配对结果支持 WebP 总耗时更低，但仍需更多时段验证。"
        if hi < 1
        else "这次配对结果显示 WebP 总耗时更高。"
        if lo > 1
        else "配对耗时比的 95% 区间跨过 1，尚不能确认 WebP 有稳定的速度收益。"
    )
    per_page = []
    defaults = []
    chart_data = []
    failures = []
    tokens = []
    for case in cases:
        loc = {x["variant"]: x for x in report["local"] if x["case"] == case["id"]}
        groups = {
            v: [x for x in rows if x["case"] == case["id"] and x["variant"] == v] for v in summaries
        }
        times = {
            v: statistics.median(x["total_seconds"] for x in g if "error" not in x)
            for v, g in groups.items()
        }
        p, w = loc["png_original"], loc["webp_q70"]
        per_page.append(
            [
                case["page"],
                f"{p['image']['bytes'] / 1024:.1f}",
                f"{w['image']['bytes'] / 1024:.1f}",
                f"{(1 - w['image']['bytes'] / p['image']['bytes']) * 100:.1f}%",
                f"{w['conversion_ms']:.1f}",
                f"{times['png_original']:.2f}",
                f"{times['webp_q70']:.2f}",
                f"{times['webp_q70'] - times['png_original']:+.2f}",
            ]
        )
        defaults.append(
            [
                case["page"],
                f"{p['default_adapter_image']['bytes'] / 1024:.1f}",
                f"{w['default_adapter_image']['bytes'] / 1024:.1f}",
                f"{p['default_adapter_image']['format']} / {w['default_adapter_image']['format']}",
                f"{p['default_adapter_image']['width']}×{p['default_adapter_image']['height']}",
            ]
        )
        chart_data.append(
            (
                case["page"],
                p["image"]["bytes"] / 1024,
                w["image"]["bytes"] / 1024,
                times["png_original"],
                times["webp_q70"],
            )
        )
    for variant in summaries:
        group = [r for r in rows if r["variant"] == variant and r.get("usage")]
        tokens.append(
            [
                variant,
                statistics.median(r["usage"]["input_tokens"] for r in group),
                statistics.median(
                    r["usage"].get("input_token_details", {}).get("cache_read", 0) for r in group
                ),
                statistics.median(r["usage"]["output_tokens"] for r in group),
            ]
        )
    for r in rows:
        if not r["passed"]:
            failures.append(
                [
                    r["case"],
                    r["repeat"],
                    r["variant"],
                    json.dumps(r.get("decision"), ensure_ascii=False),
                    r.get("error", ""),
                    r["ocr_matches"],
                    r["target_hit"],
                ]
            )
    plt.rcParams.update(
        {
            "font.family": "Microsoft YaHei",
            "axes.unicode_minus": False,
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.7))
    y = np.arange(len(cases))
    labels = [x[0] for x in chart_data]
    for ax, offset, title in [
        (axes[0], 1, "同分辨率文件大小（KiB，横轴对数刻度）"),
        (axes[1], 3, "转换 + 模型请求中位耗时（秒）"),
    ]:
        for delta, col, label, color in [
            (-0.18, offset, "原始 PNG", "#4875c7"),
            (0.18, offset + 1, "WebP Q70", "#24a08d"),
        ]:
            vals = [x[col] for x in chart_data]
            ax.barh(y + delta, vals, height=0.32, color=color, label=label)
            for i, value in enumerate(vals):
                ax.text(value * 1.025, i + delta, f"{value:.1f}", va="center", fontsize=8)
        ax.set_yticks(y, labels if offset == 1 else [""] * len(labels))
        ax.invert_yaxis()
        ax.set_title(title, pad=16)
        ax.grid(axis="x", alpha=0.15)
        ax.set_axisbelow(True)
        if offset == 1:
            ax.set_xscale("log")
            ax.set_xlim(10, max(x[1] for x in chart_data) * 2.2)
        else:
            ax.set_xlim(0, max(max(x[3], x[4]) for x in chart_data) * 1.16)
    fig.suptitle("原始 PNG → WebP 有损 Q70 · gpt-6.1-sol / medium", fontsize=16)
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="upper center", bbox_to_anchor=(0.53, 0.94), ncol=2)
    fig.text(
        0.02,
        0.02,
        f"8 页 × 2 格式 × 2 轮；1080×2400。耗时排除 {png['errors'] + webp['errors']} 次调用错误，包含上传与模型响应。",
        fontsize=10,
        color="#566779",
    )
    fig.tight_layout(rect=(0, 0.055, 1, 0.95))
    fig.savefig(out / "comparison.png", dpi=170, facecolor="white")
    plt.close(fig)
    summary_rows = []
    for title, s in [("原始 PNG", png), ("WebP quality=70", webp)]:
        successful = s["samples"] - s["errors"]
        summary_rows.append(
            [
                title,
                f"{s['median_kib']:.1f}",
                f"{s['total_bytes'] / 1024:.1f}",
                f"{s['median_conversion_ms']:.1f}",
                f"{s['median_request_seconds']:.2f}",
                f"{s['median_total_seconds']:.2f}",
                f"{s['ocr_correct']}/{successful * 3}",
                f"{s['target_hits']}/{successful}",
                f"{s['passed']}/{s['samples']}",
                s["errors"],
            ]
        )
    cards = "".join(
        f'<figure><a href="../{escape(c["file"])}"><img src="../{escape(c["file"])}" loading="lazy"></a><figcaption>{escape(c["page"])} · <a href="inputs/{escape(c["id"])}/png_original.png">PNG</a> / <a href="inputs/{escape(c["id"])}/webp_q70.webp">WebP Q70</a></figcaption></figure>'
        for c in cases
    )
    html = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>PNG 与 WebP Q70 实测</title><style>
body{{font:16px/1.75 "Microsoft YaHei",sans-serif;background:#f1f4f9;color:#20344a;margin:0}}main{{max-width:1250px;padding:40px;margin:30px auto;background:white;border-radius:16px}}h1{{font-size:32px}}h2{{margin-top:36px;border-top:1px solid #e1e7ef;padding-top:24px}}.lead{{background:#eaf4f7;padding:18px 24px;border-left:4px solid #24a08d}}.meta{{color:#697b8c}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #e3e8ef;text-align:left;vertical-align:top}}th{{white-space:nowrap;background:#edf2f8}}.scroll{{overflow:auto}}.chart{{width:100%}}.gallery{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}}figure{{margin:0}}figure img{{width:100%;border:1px solid #e0e6ee;border-radius:6px}}figcaption{{font-size:13px}}a{{color:#295bbb}}code,pre{{background:#eef2f6}}pre{{overflow:auto;padding:16px}}@media(max-width:700px){{main{{margin:0;padding:20px}}.gallery{{grid-template-columns:repeat(2,1fr)}}}}</style><main>
<p class="meta">2026-10-10 · 小米 M2002J9E / Android 12 · 1080×2400 原始截图</p><h1>原始 PNG 转 WebP Q70：大小与耗时</h1>
<div class="lead"><p>8 张图合计体积减少 <strong>{saved * 100:.1f}%</strong>。WebP 转换中位耗时 <strong>{webp["median_conversion_ms"]:.0f} ms</strong>，包含 PNG 解码与 WebP 编码。</p><p>模型请求加转换的总耗时中位数：PNG <strong>{png["median_total_seconds"]:.2f}s</strong>，WebP <strong>{webp["median_total_seconds"]:.2f}s</strong>（差 {median_delta:+.2f}s）。{speed_text}</p></div>
<p>模型严格使用 <code>gpt-6.1-sol</code>，推理程度 <code>medium</code>；8 页 × 2 格式 × 2 轮，共 {len(rows)} 次计时请求。WebP 参数 <code>quality=70, method=4, lossless=False</code>，保持原始尺寸。quality=70 是编码器质量参数，不是承诺体积减少 70%。</p>
<p>调用客户端：<code>{escape(report["identity"]["codex_version"])}</code>；可执行文件：<code>{escape(report["identity"]["codex_binary"])}</code>。PATH 中的 0.146 旧客户端拒绝此模型，已明确指定桌面应用现有的新客户端；旧客户端预热失败未计入结果。</p>
<img class="chart" src="comparison.png" alt="每个页面的PNG和WebP文件大小及总耗时">
<h2>汇总</h2>{table(["输入", "体积中位 KiB", "8图合计 KiB", "转换中位 ms", "请求中位 s", "总耗时中位 s", "OCR正确（成功调用）", "定位命中（成功调用）", "整例通过（全部尝试）", "调用错误"], summary_rows)}
<p>原计划 32 次请求中有 {png["errors"] + webp["errors"]} 次调用错误；OCR 与定位质量只在成功返回的调用中计分，整例通过率保留错误调用作为失败。时间中位数排除调用错误；配对分析进一步只采用两种格式均成功返回的完整配对。所有失败记录保留在原始数据中。</p>
<p>两条路径都先读取 PNG 到内存。PNG 路径直接发送原始字节；WebP 路径每次实际执行转换后再发送。请求耗时包含 base64、适配器、App Server、上传、服务排队、推理与输出；当前 SDK 没有单独的上传计时，因此不能把“请求耗时”写成“上传耗时”。总耗时不含设备截图、磁盘读取、导航和测试结果写盘。</p>
<h2>逐页结果</h2>{table(["页面", "PNG KiB", "WebP KiB", "体积减少", "转换 ms", "PNG总耗时 s", "WebP总耗时 s", "差值 s"], per_page)}
<p>各页时间是最多两次成功请求的中位数（调用错误排除）；转换列是同一截图预热一次后的 10 次本地测量中位数。PNG 与 WebP 顺序在页面间交替，第二轮反转，以减少固定先后顺序偏差。</p>
<h2>速度差异是否可靠</h2><p>{len(analysis["pairs"])} 个有效配对样本中，WebP 更快的有 {analysis["faster_pairs"]} 个。对同一页同一轮计算 WebP/PNG 总耗时比，按页面聚类做 10,000 次 bootstrap：几何平均比 <strong>{analysis["geomean_ratio"]:.3f}</strong>，95% 区间 <strong>[{lo:.3f}, {hi:.3f}]</strong>。{speed_text} 此区间只反映这 8 页与本次时段，不能代表长期服务延迟。</p>
{table(["格式", "输入token中位数", "缓存token中位数", "输出token中位数"], tokens)}
<p>缓存没有禁用，网络和服务负载不可控。输入 tokens 是整个请求，并非纯图片 tokens；图像压缩字节数与视觉 tokens 不是同一个量。服务端是否再次解码/缩放由服务决定，本测试验证的是客户端适配边界保持原格式与原始像素尺寸。</p>
<h2>项目识别速度受哪些因素影响</h2><p>实际耗时依次包括：设备截图与层级获取 → 图像解码、缩放、编码 → 传输与排队 → 模型处理输入、推理和生成输出。PNG/WebP 格式主要改变文件字节数和本地编解码成本；分辨率、图中文字密度及服务端 detail 处理影响视觉输入规模，模型和推理程度、提示词与输出长度、缓存、服务负载共同影响请求时间。单凭体积压缩率不能推算总耗时降幅。</p>
<p>补充测量同一静止桌面：ADB 原始 PNG 截图中位 1.347s，helper 的 JPEG + 层级快照中位 0.153s（各 5 次有效测量，首轮预热排除）。两条路径的实现与工作内容不同，不能将差值全部归因于 PNG/JPEG 格式；这说明截图获取路径也需要单独优化。此项没有计入上面的模型比较。</p>
<h2>为什么需要绕过当前客户端的二次转换</h2><p>项目 helper 平时输出 JPEG Q80。当前 codex-client-provider 0.1.0 遇到最长边超过 1600 或体积超过 768 KiB 的输入，会缩放并转成 JPEG Q82（若仍超限则继续降低质量/尺寸）。直接把这批原始 PNG 换为 WebP 后走默认链路，二者最终都会变成 720×1600 JPEG：</p>
{table(["页面", "PNG走默认链路后 KiB", "WebP走默认链路后 KiB", "实际格式", "实际尺寸"], defaults)}
<p>为了真正对比 PNG 上传与 WebP 上传，本次只在独立基准进程中将最长边限制设为 {report["identity"]["max_edge"]}、字节限制设为 {report["identity"]["max_bytes"] // 1024 // 1024} MiB；逐图断言适配器输出与输入字节完全一致。没有修改项目生产配置。这组速度结论对应保留原格式的链路，不能直接套用到当前默认链路；上表为默认适配器本地结果，未额外调用模型。</p>
<h2>识别质量与样本</h2><p>每页指定 3 个独立文字区域和 1 个定位目标。OCR 逐项精确匹配（只统一 NFKC 与空白），定位点必须落在已核验原始像素框内。提示词不包含 OCR 答案、目标框或页面名称。全对才算整例通过；这是固定图片的读取和定位测试，不是完整导航成功率。</p>
<div class="gallery">{cards}</div>
<p>截图由 ARTEMIS 探索和 ADB 采集，并逐张视觉核对。探索 trace：<code>3cfb8c37-3ad7-48cc-9982-a5adfb7591cd</code>；Pro 在前两页后主动停止，余下页面由主进程通过 ARTEMIS 观测及 ADB 补齐。第 8 页是计算器首次使用说明，没有同意条款；最终已回桌面。</p>
{table(["页面", "轮次", "格式", "实际输出", "调用错误", "OCR匹配", "定位命中"], failures) if failures else "<p>本次全部 OCR 与定位样本通过。</p>"}
{recovery_html}
<h2>原始数据与复现</h2><p><a href="results.json">完整逐次结果、图片哈希、用量与模型回包</a> · <a href="analysis.json">配对分析</a> · <a href="../manifest.json">页面标注</a> · <a href="../capture-timings.json">截图环节补充计时</a>。前一轮旧模型的 16 次探索请求保存在 <code>../run/results.json</code>，没有混入本次统计。</p>
<pre>.venv/Scripts/python.exe scripts/benchmark_png_webp.py --manifest artifacts/image-benchmark/2026-10-10/manifest.json --output artifacts/image-benchmark/2026-10-10/png-webp70-gpt61 --model gpt-6.1-sol --effort medium --codex-binary "{escape(report["identity"]["codex_binary"])}"
.venv/Scripts/python.exe scripts/report_png_webp.py artifacts/image-benchmark/2026-10-10/png-webp70-gpt61/results.json</pre>
<p>可断点续跑，每次进程启动另做一次不计分预热；没有重试失败请求或自动换模型。参数或代码变化会拒绝续跑，使用新目录即可重新实验。</p>
<h2>参考依据</h2><p><a href="https://developers.openai.com/api/docs/models/gpt-6.1-sol">GPT-6.1 Sol 官方模型说明</a>确认图像输入与 medium 推理支持；<a href="https://developers.openai.com/api/docs/guides/images-vision">Images and vision</a>说明格式与像素/detail相关输入处理；<a href="https://developers.openai.com/api/docs/guides/latency-optimization">Latency optimization</a>说明输出长度、模型与调用次数共同影响耗时；<a href="https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html">Pillow 格式说明</a>解释 WebP quality 与 method。实际客户端行为以本地源码和本次实测为准。</p></main></html>"""
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    render(parser.parse_args().results)
