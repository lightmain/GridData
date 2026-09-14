"""Measure 3-D integer-delta robustness after whitening only low float bits."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import matplotlib
import numpy as np
from PIL import Image

matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from three_dim_diff_common import (  # noqa: E402
    StreamedThreeDimensionalDelta,
    save_bundle,
    weighted_delta_entropy,
)

DEFAULT_INPUT = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "3-dim-diff-noise"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "ThreeDimDiffNoise-cn.md"
DEFAULT_FIGURES = PROJECT_ROOT / "reports" / "figures" / "3-dim-diff-noise"
VARIANTS = ("original", "noise7", "noise8")


def files_in(path: Path, limit: int | None) -> list[Path]:
    files = sorted(p for p in path.iterdir() if p.suffix.lower() in {".tif", ".tiff"})
    if limit is not None:
        files = files[:limit]
    if not files:
        raise FileNotFoundError(f"No TIFF files in {path}")
    return files


def read_float(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        values = np.asarray(image, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError(f"Expected 2-D TIFF: {path}")
    return values.astype("<f4", copy=False)


def whiten_low_bits(words: np.ndarray, bits: int, rng: np.random.Generator) -> np.ndarray:
    mask = np.uint32((1 << bits) - 1)
    random_low = rng.integers(0, 1 << bits, size=words.shape, dtype=np.uint32)
    result = (words & ~mask) | random_low
    if not np.array_equal(result >> np.uint32(bits), words >> np.uint32(bits)):
        raise AssertionError(f"Whitening escaped the lowest {bits} bits")
    return result


def configure_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
        "axes.unicode_minus": False, "figure.dpi": 130, "savefig.dpi": 180,
    })


def plot_results(result: dict, output: Path) -> None:
    configure_style()
    output.mkdir(parents=True, exist_ok=True)
    d = result["distributions"]
    labels = ["原始", "低 7 位白化", "低 8 位白化"]
    raw_entropy = [d[f"{v}_raw"]["entropy_bits_per_value"] for v in VARIANTS]
    delta_entropy = [d[f"{v}_interior"]["entropy_bits_per_value"] for v in VARIANTS]
    zero = [d[f"{v}_interior"]["zero_fraction"] for v in VARIANTS]
    mult64 = [d[f"{v}_interior"]["multiple_of_64_fraction"] for v in VARIANTS]
    x = np.arange(3)
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)
    axes[0].bar(x - .18, raw_entropy, .36, label="原始 word")
    axes[0].bar(x + .18, delta_entropy, .36, label="内部三维差分")
    axes[0].set_xticks(x, labels)
    axes[0].set_ylabel("bit / value")
    axes[0].set_title("低位白化对边际熵的影响")
    axes[0].legend()
    axes[1].plot(labels, zero, "o-", label="差分为 0")
    axes[1].plot(labels, mult64, "s-", label="64 的倍数")
    axes[1].set_yscale("log")
    axes[1].set_ylim(3e-4, 1.1)
    axes[1].set_ylabel("fraction")
    axes[1].set_title("结构性概率随噪声退化（对数轴）")
    axes[1].legend()
    fig.suptitle("ERA5 float32 低位白噪声与三维差分")
    fig.savefig(output / "noise_degradation.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    widths = []
    for variant in VARIANTS:
        summary = d[f"{variant}_interior"]
        total = summary["count"]
        widths.append(sum(c for w, c in summary["signed_zigzag_bit_width"].items() if int(w) <= 16) / total)
    fig, ax = plt.subplots(figsize=(8.5, 4.7), constrained_layout=True)
    ax.bar(labels, widths, color=["#2563eb", "#d97706", "#be123c"])
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("fraction")
    ax.set_title("内部差分 ZigZag 后不超过 16 bit 的比例")
    ax.bar_label(ax.containers[0], labels=[f"{v:.2%}" for v in widths])
    fig.savefig(output / "zigzag_16bit_fraction.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_report(path: Path, result: dict) -> None:
    d = result["distributions"]
    rows = []
    labels = {"original": "原始", "noise7": "最低 7 位白化", "noise8": "最低 8 位白化"}
    for variant in VARIANTS:
        raw, delta = d[f"{variant}_raw"], d[f"{variant}_interior"]
        rows.append(
            f"| {labels[variant]} | {raw['entropy_bits_per_value']:.4f} | "
            f"{delta['entropy_bits_per_value']:.4f} | {delta['unique_count']:,} | "
            f"{delta['zero_fraction']:.6%} | {delta['multiple_of_64_fraction']:.6%} | "
            f"{result['weighted_delta_entropy'][variant]:.4f} |"
        )
    noise = result["noise_error"]
    lines = [
        "# ERA5 float32 低位白噪声与三维差分统计", "", "## 方法", "",
        "保持每个 float32 位模式的高位不变，把最低 7 位或最低 8 位替换为时空独立的",
        "均匀随机整数。随机种子为 `2311`。这是一种严格限制在指定低位内的白化实验，",
        "不是可能产生高位进位的高斯数值加法。", "",
        f"形状：`{tuple(result['shape'])}`；处理耗时 `{result['elapsed_seconds']:.2f}` 秒。", "",
        "## 温度扰动", "",
        f"- 7-bit：RMSE `{noise['noise7']['rmse_kelvin']:.8f}` K，最大绝对误差 "
        f"`{noise['noise7']['maximum_absolute_error_kelvin']:.8f}` K。",
        f"- 8-bit：RMSE `{noise['noise8']['rmse_kelvin']:.8f}` K，最大绝对误差 "
        f"`{noise['noise8']['maximum_absolute_error_kelvin']:.8f}` K。", "",
        "## 核心统计", "",
        "| 数据 | 原始 word 熵 | 内部差分熵 | 内部不同值 | 内部 0 占比 | 内部 64 倍数 | 全差分加权熵 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |", *rows, "", "## 解释", "",
        "- 白化只破坏低位规则，不改变符号、指数和高位尾数，因此宏观温度场保持不变。",
        "- 原始与噪声版本之间的差分熵变化，直接反映现有收益中有多少来自 GRIB 定点尾部。",
        "- 若噪声后仍明显低于原始 word 熵，剩余收益主要来自温度场的时空相关性。",
        "- 边际熵不利用邻域上下文，因此只是实际压缩器的比较基线。", "",
        "## 输出", "", "`results.json` 保存摘要；`histograms/*.csv.gz` 保存三个版本全部区域的精确频数。",
        "", "## 图表", "",
        "![噪声退化](figures/3-dim-diff-noise/noise_degradation.png)", "",
        "![ZigZag 16-bit 比例](figures/3-dim-diff-noise/zigzag_16bit_fraction.png)",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--figures", type=Path, default=DEFAULT_FIGURES)
    p.add_argument("--limit", type=int)
    p.add_argument("--seed", type=int, default=2311)
    p.add_argument("--progress-every", type=int, default=25)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if (args.output / "results.json").exists() and not args.overwrite:
        raise FileExistsError("results.json exists; pass --overwrite")
    files = files_in(args.input, args.limit)
    analyses = {v: StreamedThreeDimensionalDelta() for v in VARIANTS}
    rng7 = np.random.default_rng(args.seed)
    rng8 = np.random.default_rng(args.seed)
    errors = {v: {"sum_square": 0.0, "max": 0.0, "count": 0} for v in ("noise7", "noise8")}
    started = time.perf_counter()
    for t, file in enumerate(files):
        original = read_float(file)
        words = original.view("<u4")
        variants = {
            "original": words,
            "noise7": whiten_low_bits(words, 7, rng7),
            "noise8": whiten_low_bits(words, 8, rng8),
        }
        for name, variant in variants.items():
            analyses[name].update(variant.astype(np.int64))
            if name != "original":
                noisy = variant.astype("<u4", copy=False).view("<f4")
                difference = noisy.astype(np.float64) - original.astype(np.float64)
                errors[name]["sum_square"] += float(np.dot(difference.ravel(), difference.ravel()))
                errors[name]["max"] = max(errors[name]["max"], float(np.max(np.abs(difference))))
                errors[name]["count"] += difference.size
        if args.progress_every and ((t + 1) % args.progress_every == 0 or t + 1 == len(files)):
            print(f"Processed {t + 1}/{len(files)} TIFFs", flush=True)
    distributions = {}
    bases = {}
    for variant, analysis in analyses.items():
        bases[variant] = analysis.distributions()
        distributions.update({f"{variant}_{k}": v for k, v in bases[variant].items()})
    noise_error = {
        name: {
            "rmse_kelvin": math.sqrt(item["sum_square"] / item["count"]),
            "maximum_absolute_error_kelvin": item["max"],
        }
        for name, item in errors.items()
    }
    metadata = {
        "method": "independent_uniform_low_bit_whitening_then_uint32_3d_delta",
        "seed": args.seed, "shape": list(analyses["original"].shape),
        "input": str(args.input.resolve()), "noise_error": noise_error,
        "high_bits_unchanged": True, "elapsed_seconds": time.perf_counter() - started,
    }
    result = save_bundle(args.output, metadata, distributions)
    result["weighted_delta_entropy"] = {}
    for variant in VARIANTS:
        view = {"distributions": {k: result["distributions"][f"{variant}_{k}"] for k in bases[variant]}}
        result["weighted_delta_entropy"][variant] = weighted_delta_entropy(view)
    (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(args.report, result)
    plot_results(result, args.figures)
    print(f"Wrote {args.output / 'results.json'}")
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
