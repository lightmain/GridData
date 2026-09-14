"""Plot the uint32/3-D-delta distribution analysis with Matplotlib."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import PercentFormatter


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = (
    PROJECT_ROOT / "data" / "ERA5-temperature-May2026_uint32_3d_diff" / "results.json"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports" / "figures" / "uint32_3d_diff"

COLORS = {
    "raw": "#64748b",
    "interior": "#2563eb",
    "planes": "#0f766e",
    "edges": "#d97706",
    "vertex": "#be123c",
    "accent": "#7c3aed",
}


def configure_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [
                "Microsoft YaHei",
                "SimHei",
                "Noto Sans CJK SC",
                "DejaVu Sans",
            ],
            "axes.unicode_minus": False,
            "axes.titleweight": "bold",
            "figure.dpi": 130,
            "savefig.dpi": 180,
            "savefig.bbox": "tight",
        }
    )


def load_results(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_histogram(path: Path) -> tuple[np.ndarray, np.ndarray]:
    values: list[int] = []
    counts: list[int] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            values.append(int(row["value"]))
            counts.append(int(row["count"]))
    return np.asarray(values, dtype=np.int64), np.asarray(counts, dtype=np.int64)


def weighted_delta_entropy(distributions: dict[str, Any]) -> float:
    names = ("interior", "planes", "edges", "vertex")
    count = sum(distributions[name]["count"] for name in names)
    return sum(
        distributions[name]["count"] * distributions[name]["entropy_bits_per_value"]
        for name in names
    ) / count


def save_figure(figure: plt.Figure, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, facecolor="white")
    plt.close(figure)
    print(f"Wrote {path}")


def plot_overview(result: dict[str, Any], path: Path) -> None:
    distributions = result["distributions"]
    total = math.prod(result["shape"])
    region_names = ("interior", "planes", "edges", "vertex")
    region_labels = ("三维内部", "三个二维面", "三条一维棱", "顶点")
    colors = [COLORS[name] for name in region_names]

    figure, axes = plt.subplots(2, 2, figsize=(13, 8.2), constrained_layout=True)
    figure.suptitle("ERA5 uint32 三维差分：区域结构与离散度", fontsize=17)

    counts = np.asarray([distributions[name]["count"] for name in region_names])
    axes[0, 0].barh(region_labels, counts, color=colors)
    axes[0, 0].set_xscale("log")
    axes[0, 0].set_title("四类区域规模（对数坐标）")
    axes[0, 0].set_xlabel("整数个数")
    for index, count in enumerate(counts):
        fraction = count / total
        fraction_label = (
            f"{fraction:.6%}" if fraction >= 1e-8 else f"{100 * fraction:.3e}%"
        )
        axes[0, 0].text(
            count * 1.08,
            index,
            fraction_label,
            va="center",
            fontsize=9,
        )

    entropy_names = ("raw_uint32", "interior", "planes", "edges")
    entropy_labels = ("原始 uint32", "内部", "二维面", "一维棱")
    entropies = [distributions[name]["entropy_bits_per_value"] for name in entropy_names]
    axes[0, 1].bar(
        entropy_labels,
        entropies,
        color=[COLORS["raw"], COLORS["interior"], COLORS["planes"], COLORS["edges"]],
    )
    axes[0, 1].set_title("单值频数的 Shannon 熵")
    axes[0, 1].set_ylabel("bit / value")
    axes[0, 1].set_ylim(0, 23)
    axes[0, 1].bar_label(axes[0, 1].containers[0], fmt="%.2f")

    unique_counts = [distributions[name]["unique_count"] for name in entropy_names]
    axes[1, 0].bar(
        entropy_labels,
        unique_counts,
        color=[COLORS["raw"], COLORS["interior"], COLORS["planes"], COLORS["edges"]],
    )
    axes[1, 0].set_yscale("log")
    axes[1, 0].set_title("不同整数数量（对数坐标）")
    axes[1, 0].set_ylabel("unique values")
    axes[1, 0].bar_label(
        axes[1, 0].containers[0],
        labels=[f"{value:,}" for value in unique_counts],
        padding=3,
        fontsize=9,
    )

    zero_names = ("interior", "planes", "edges", "latitude_zero_face")
    zero_labels = ("内部", "三个面", "三条棱", "lat=0 面")
    zero_fractions = [distributions[name]["zero_fraction"] for name in zero_names]
    axes[1, 1].bar(
        zero_labels,
        zero_fractions,
        color=[COLORS["interior"], COLORS["planes"], COLORS["edges"], COLORS["accent"]],
    )
    axes[1, 1].set_title("差分值为 0 的比例")
    axes[1, 1].set_ylabel("占本区域比例")
    axes[1, 1].set_ylim(0, 1.08)
    axes[1, 1].yaxis.set_major_formatter(PercentFormatter(1.0))
    axes[1, 1].bar_label(
        axes[1, 1].containers[0],
        labels=[f"{value:.2%}" for value in zero_fractions],
        padding=3,
    )

    save_figure(figure, path)


def plot_interior_distribution(
    result: dict[str, Any], histogram_path: Path, path: Path
) -> None:
    values, counts = read_histogram(histogram_path)
    probabilities = counts / counts.sum()
    summary = result["distributions"]["interior"]

    figure, axes = plt.subplots(1, 2, figsize=(14, 5.2), constrained_layout=True)
    figure.suptitle("占 99.66% 张量的内部三重差分分布", fontsize=17)

    order = np.argsort(counts)[-25:]
    top_values = values[order]
    top_probabilities = probabilities[order]
    label_colors = [
        COLORS["interior"] if value >= 0 else "#60a5fa" for value in top_values
    ]
    axes[0].barh([f"{value:,}" for value in top_values], top_probabilities, color=label_colors)
    axes[0].set_title("最高频的 25 个整数")
    axes[0].set_xlabel("占内部区比例")
    axes[0].xaxis.set_major_formatter(PercentFormatter(1.0))
    axes[0].text(
        0.98,
        0.04,
        "蓝：非负值    浅蓝：负值",
        transform=axes[0].transAxes,
        ha="right",
        fontsize=9,
        color="#475569",
    )

    nonzero = values != 0
    axes[1].scatter(
        values[nonzero],
        probabilities[nonzero],
        s=5,
        alpha=0.35,
        color=COLORS["interior"],
        linewidths=0,
        rasterized=True,
    )
    axes[1].scatter(
        [0],
        [summary["zero_fraction"]],
        s=55,
        color=COLORS["vertex"],
        zorder=3,
        label=f"0: {summary['zero_fraction']:.2%}",
    )
    axes[1].set_xscale("symlog", linthresh=64, linscale=0.8)
    axes[1].set_yscale("log")
    axes[1].set_xticks((-100_000, -10_000, -1_000, -100, 100, 1_000, 10_000, 100_000))
    axes[1].set_title("全部 104,243 种内部差分值")
    axes[1].set_xlabel("差分整数（x 为 symlog）")
    axes[1].set_ylabel("精确频率（y 为 log）")
    axes[1].legend(frameon=True)
    axes[1].axvline(0, color="#94a3b8", linewidth=0.8)

    save_figure(figure, path)


def width_arrays(summary: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    width_counts = {int(width): int(count) for width, count in summary["signed_zigzag_bit_width"].items()}
    widths = np.arange(0, max(width_counts) + 1)
    counts = np.asarray([width_counts.get(int(width), 0) for width in widths])
    return widths, counts / counts.sum()


def plot_bitwidth_and_potential(result: dict[str, Any], path: Path) -> None:
    distributions = result["distributions"]
    total = math.prod(result["shape"])
    figure, axes = plt.subplots(1, 2, figsize=(14, 5.4), constrained_layout=True)
    figure.suptitle("ZigZag 位宽与三维差分的压缩潜力", fontsize=17)

    for name, label, color in (
        ("interior", "内部", COLORS["interior"]),
        ("planes", "二维面", COLORS["planes"]),
        ("edges", "一维棱", COLORS["edges"]),
    ):
        widths, probabilities = width_arrays(distributions[name])
        axes[0].plot(
            widths,
            np.cumsum(probabilities),
            marker="o",
            markersize=3,
            linewidth=2,
            label=label,
            color=color,
        )
    axes[0].axvline(8, color="#94a3b8", linestyle="--", linewidth=1)
    axes[0].axvline(16, color="#94a3b8", linestyle="--", linewidth=1)
    axes[0].text(8.2, 0.13, "8 bit", color="#64748b")
    axes[0].text(16.2, 0.13, "16 bit", color="#64748b")
    axes[0].set_xlim(0, 32)
    axes[0].set_ylim(0, 1.02)
    axes[0].set_title("最小 ZigZag 位宽的累积分布")
    axes[0].set_xlabel("不超过该 bit-width")
    axes[0].set_ylabel("累计覆盖率")
    axes[0].yaxis.set_major_formatter(PercentFormatter(1.0))
    axes[0].legend(loc="lower right")

    tiff_bytes = sum(
        file.stat().st_size
        for file in Path(result["input_dir"]).iterdir()
        if file.is_file() and file.suffix.lower() in {".tif", ".tiff"}
    )
    tiff_bits_per_value = tiff_bytes * 8 / total
    delta_entropy = weighted_delta_entropy(distributions)
    labels = ("未压缩\nfloat32", "当前 TIFF\n实际大小", "原始整数\n边际熵", "三维差分\n边际熵")
    bits = (
        32.0,
        tiff_bits_per_value,
        distributions["raw_uint32"]["entropy_bits_per_value"],
        delta_entropy,
    )
    bars = axes[1].bar(
        labels,
        bits,
        color=[COLORS["raw"], "#475569", "#94a3b8", COLORS["accent"]],
    )
    axes[1].set_title("码率尺度比较")
    axes[1].set_ylabel("bit / value")
    axes[1].set_ylim(0, 35)
    axes[1].bar_label(bars, labels=[f"{value:.2f}" for value in bits], padding=3)
    axes[1].text(
        0.5,
        -0.22,
        f"差分边际熵相对 float32 为 {32 / delta_entropy:.2f}×；"
        f"相对当前 TIFF 仍有约 {tiff_bits_per_value / delta_entropy:.2f}× 的理论空间",
        transform=axes[1].transAxes,
        ha="center",
        fontsize=10,
        color="#334155",
    )
    axes[1].text(
        0.5,
        -0.31,
        "边际熵不是实际压缩结果；编码头、异常值和上下文模型都会改变最终码率",
        transform=axes[1].transAxes,
        ha="center",
        fontsize=9,
        color="#64748b",
    )

    save_figure(figure, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = load_results(args.results)
    histogram_dir = args.results.parent / "histograms"
    configure_style()
    plot_overview(result, args.output_dir / "overview.png")
    plot_interior_distribution(
        result,
        histogram_dir / "interior.csv.gz",
        args.output_dir / "interior_distribution.png",
    )
    plot_bitwidth_and_potential(result, args.output_dir / "bitwidth_and_potential.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
