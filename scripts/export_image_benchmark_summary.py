"""Export only numeric aggregate metrics from the two local image experiments."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export(source, destination):
    experiments = []
    for label, folder, variants in (
        ("A", "png-webp70-gpt61", ("png_original", "webp_q70")),
        ("B", "webp720-gpt61-v2", ("webp_native_q70", "webp_720_q70")),
    ):
        report = json.loads((source / folder / "results.json").read_text(encoding="utf-8"))
        analysis = json.loads((source / folder / "analysis.json").read_text(encoding="utf-8"))
        assert report["identity"]["model"] == "gpt-6.1-sol"
        assert report["identity"]["effort"] == "medium"
        summaries = []
        for variant in variants:
            raw = next(s for s in report["summary"] if s["variant"] == variant)
            attempts = raw.get("attempts", raw.get("samples"))
            successful = attempts - raw["errors"]
            metrics = {
                "variant": variant,
                "total_bytes_8_images": raw["total_bytes"],
                "attempts": attempts,
                "successful": successful,
                "errors_before_inference": raw["errors"],
                "text_checks_correct": raw["ocr_correct"],
                "text_checks_evaluated": successful * 3,
                "target_hits": raw["target_hits"],
                "target_checks_evaluated": successful,
                "median_image_kib": round(raw["median_kib"], 3),
                "median_conversion_ms": round(raw["median_conversion_ms"], 3),
                "median_request_seconds": round(raw["median_request_seconds"], 3),
                "median_total_seconds": round(raw["median_total_seconds"], 3),
            }
            assert all(isinstance(v, (int, float)) for k, v in metrics.items() if k != "variant")
            summaries.append(metrics)
        experiments.append(
            {
                "experiment": label,
                "pages": 8,
                "rounds": 2,
                "variants": summaries,
                "paired_total_time": {
                    "pairs": len(analysis["pairs"]),
                    "geometric_mean_ratio": round(analysis["geomean_ratio"], 6),
                    "page_cluster_bootstrap_ci95": [round(v, 6) for v in analysis["ratio_ci95"]],
                    "faster_pairs": analysis["faster_pairs"],
                },
            }
        )
    public = {
        "date": "2026-10-10",
        "model": "gpt-6.1-sol",
        "reasoning_effort": "medium",
        "original_dimensions": [1080, 2400],
        "resized_dimensions": [720, 1600],
        "webp_quality": 70,
        "webp_method": 4,
        "webp_lossless": False,
        "privacy": "Aggregate only; no screenshots, raw output, identifiers or local paths.",
        "timing": "Request includes adapter, App Server, upload, queue, inference and output. "
        "Total also includes decode, resize, encode and base64. "
        "Capture, source reads, report writes, cleanup and warmups excluded.",
        "recognition_scope": "Three text checks and one target hit per successful request.",
        "recovery_requests_excluded_from_experiment_A": 4,
        "recovery_requests_passed": 4,
        "conclusion": "Smaller payload confirmed; neither paired confidence interval "
        "establishes a stable speedup. Experiments measured separately.",
        "experiments": experiments,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=ROOT / "artifacts/image-benchmark/2026-10-10"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/benchmarks/image-input-2026-10-10.json"
    )
    args = parser.parse_args()
    export(args.source, args.output)
