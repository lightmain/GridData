"""Remove per-time float32 tails, then analyse reversible 3-D integer deltas."""

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
    ExactDistribution,
    StreamedThreeDimensionalDelta,
    mixed_2d_delta,
    save_bundle,
    weighted_delta_entropy,
)

DEFAULT_INPUT = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "3-dim-diff-cut-tail-float"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "ThreeDimDiffCutTailFloat-cn.md"
DEFAULT_FIGURES = PROJECT_ROOT / "reports" / "figures" / "3-dim-diff-cut-tail-float"


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
    if values.dtype.kind != "f" or values.dtype.itemsize != 4 or values.ndim != 2:
        raise ValueError(f"Expected 2-D float32 TIFF: {path} {values.dtype} {values.shape}")
    return values.astype("<f4", copy=False)


def cell_min_max(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    corners = (values[:-1, :-1], values[:-1, 1:], values[1:, :-1], values[1:, 1:])
    return np.minimum.reduce(corners), np.maximum.reduce(corners)


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
    names = ["cut_raw", "cut_interior", "interior_all_below", "interior_all_above", "interior_crossing"]
    labels = ["裁尾原值", "全部内部", "全<256", "全>256", "跨界"]
    entropy = [d[n]["entropy_bits_per_value"] for n in names]
    multiples = [d[n]["multiple_of_128_fraction"] for n in names]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)
    axes[0].bar(labels, entropy, color=["#64748b", "#2563eb", "#0f766e", "#7c3aed", "#d97706"])
    axes[0].set_ylabel("bit / value")
    axes[0].set_title("裁尾前值与内部差分熵")
    axes[0].bar_label(axes[0].containers[0], fmt="%.2f")
    axes[1].bar(labels[1:], multiples[1:], color=["#2563eb", "#0f766e", "#7c3aed", "#d97706"])
    axes[1].set_ylim(0, 1.05)
    axes[1].set_ylabel("fraction")
    axes[1].set_title("内部差分为 128 倍数的比例")
    axes[1].bar_label(axes[1].containers[0], labels=[f"{x:.1%}" for x in multiples[1:]])
    fig.suptitle("float32 按时间片裁尾后的三维差分")
    fig.savefig(output / "entropy_and_multiples.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    tail_names = ["tail_below", "tail_below_delta", "tail_above", "tail_above_delta"]
    tail_labels = ["低温尾部", "低温尾部Δt", "高温尾部", "高温尾部Δt"]
    values = [d[n]["entropy_bits_per_value"] for n in tail_names]
    fig, ax = plt.subplots(figsize=(9, 4.7), constrained_layout=True)
    ax.bar(tail_labels, values, color=["#0f766e", "#5eead4", "#7c3aed", "#c4b5fd"])
    ax.set_ylabel("bit / time step")
    ax.set_title("两个一维尾部序列：差分前后熵")
    ax.bar_label(ax.containers[0], fmt="%.3f")
    fig.savefig(output / "tail_sequence_entropy.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_report(path: Path, result: dict) -> None:
    d = result["distributions"]
    rows = []
    for name, label in (
        ("cut_raw", "裁尾 uint32"), ("cut_interior", "全部内部差分"),
        ("interior_all_below", "stencil 全 <256 K"),
        ("interior_all_above", "stencil 全 >256 K"),
        ("interior_crossing", "跨越/接触 256 K"),
        ("tail_below", "低温 7-bit 尾部"), ("tail_below_delta", "低温尾部 Δt"),
        ("tail_above", "高温 6-bit 尾部"), ("tail_above_delta", "高温尾部 Δt"),
    ):
        s = d[name]
        rows.append(
            f"| {label} | {s['count']:,} | {s['unique_count']:,} | {s['zero_fraction']:.6%} | "
            f"{s['multiple_of_64_fraction']:.6%} | {s['multiple_of_128_fraction']:.6%} | "
            f"{s['entropy_bits_per_value']:.4f} |"
        )
    lines = [
        "# ERA5 float32 分区裁尾与三维差分统计", "", "## 方法", "",
        "每个小时分别保存 `<256 K` 的最低 7 位和 `>256 K` 的最低 6 位，然后把相应",
        "尾部清零。全部时间片均通过区域内尾部唯一性和逐元素 bit-exact 恢复校验。",
        "对裁尾位模式按 `time, latitude, longitude` 做可逆三维前向差分。", "",
        f"形状：`{tuple(result['shape'])}`；处理耗时 `{result['elapsed_seconds']:.2f}` 秒。",
        f"精确 256 K 值共 `{result['tail_validation']['exact_256_count']:,}` 个。", "",
        "## 核心统计", "",
        "| 对象 | 数量 | 不同值 | 0 占比 | 64 倍数 | 128 倍数 | 熵 (bit/value) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |", *rows, "", "## 观察", "",
        f"- 裁尾后全部输入整数均为 64 的倍数；低于 256 K 的输入均为 128 的倍数。",
        f"- 内部差分熵为 `{d['cut_interior']['entropy_bits_per_value']:.4f} bit/value`，"
        f"0 占 `{d['cut_interior']['zero_fraction']:.6%}`。",
        f"- 完整差分张量分区加权边际熵为 `{result['weighted_delta_entropy']:.4f} bit/value`。",
        f"- 低温/高温尾部序列原始熵分别为 `{d['tail_below']['entropy_bits_per_value']:.4f}`/"
        f"`{d['tail_above']['entropy_bits_per_value']:.4f}`，时间差分后为 "
        f"`{d['tail_below_delta']['entropy_bits_per_value']:.4f}`/"
        f"`{d['tail_above_delta']['entropy_bits_per_value']:.4f}`。",
        "- 清尾消除了 256 K 两侧斜率不同所导致的非 64 倍数，但跨界 stencil 不保证为 128 倍数。",
        "", "## 输出", "", "`results.json` 保存摘要和 744 个尾部值；完整频数位于 `histograms/`。",
        "", "## 图表", "",
        "![裁尾差分熵与倍数结构](figures/3-dim-diff-cut-tail-float/entropy_and_multiples.png)", "",
        "![尾部序列熵](figures/3-dim-diff-cut-tail-float/tail_sequence_entropy.png)",
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
    p.add_argument("--progress-every", type=int, default=25)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if (args.output / "results.json").exists() and not args.overwrite:
        raise FileExistsError("results.json exists; pass --overwrite")
    files = files_in(args.input, args.limit)
    analysis = StreamedThreeDimensionalDelta()
    tail_below_dist, tail_above_dist = ExactDistribution(), ExactDistribution()
    tail_below_delta, tail_above_delta = ExactDistribution(), ExactDistribution()
    categories = {name: ExactDistribution() for name in ("interior_all_below", "interior_all_above", "interior_crossing")}
    tails_below: list[int] = []
    tails_above: list[int] = []
    previous_tails: tuple[int, int] | None = None
    previous_values: np.ndarray | None = None
    exact_256 = 0
    started = time.perf_counter()
    expected_shape = None
    for t, file in enumerate(files):
        values = read_float(file)
        if expected_shape is None:
            expected_shape = values.shape
        elif values.shape != expected_shape:
            raise ValueError(f"Shape changed in {file}")
        words = values.view("<u4")
        below, above, equal = values < 256, values > 256, values == 256
        rb = np.unique(words[below] & np.uint32(127))
        ra = np.unique(words[above] & np.uint32(63))
        if rb.size != 1 or ra.size != 1:
            raise AssertionError(f"Tail is not constant in {file}: below={rb}, above={ra}")
        cb, ca = int(rb[0]), int(ra[0])
        exact_256 += int(np.count_nonzero(equal))
        cut = words.copy()
        cut[below] &= np.uint32(0xFFFFFF80)
        cut[above] &= np.uint32(0xFFFFFFC0)
        restored = cut.copy()
        restored[below] |= np.uint32(cb)
        restored[above] |= np.uint32(ca)
        if not np.array_equal(restored, words):
            raise AssertionError(f"Bit-exact tail restoration failed: {file}")
        if np.any(cut % 64) or np.any(cut[below] % 128):
            raise AssertionError(f"Cleared words violate divisibility: {file}")
        temporal = analysis.update(cut.astype(np.int64))
        tails_below.append(cb); tails_above.append(ca)
        tail_below_dist.update([cb]); tail_above_dist.update([ca])
        if previous_tails is not None:
            tail_below_delta.update([cb - previous_tails[0]])
            tail_above_delta.update([ca - previous_tails[1]])
        if temporal is not None:
            assert previous_values is not None
            residual = mixed_2d_delta(temporal)
            prev_min, prev_max = cell_min_max(previous_values)
            curr_min, curr_max = cell_min_max(values)
            all_below = (prev_max < 256) & (curr_max < 256)
            all_above = (prev_min > 256) & (curr_min > 256)
            crossing = ~(all_below | all_above)
            categories["interior_all_below"].update(residual[all_below])
            categories["interior_all_above"].update(residual[all_above])
            categories["interior_crossing"].update(residual[crossing])
        previous_tails = (cb, ca)
        previous_values = values
        if args.progress_every and ((t + 1) % args.progress_every == 0 or t + 1 == len(files)):
            print(f"Processed {t + 1}/{len(files)} TIFFs", flush=True)
    base = analysis.distributions()
    distributions = {f"cut_{k}": v for k, v in base.items()}
    distributions.update(categories)
    distributions.update({
        "tail_below": tail_below_dist, "tail_above": tail_above_dist,
        "tail_below_delta": tail_below_delta, "tail_above_delta": tail_above_delta,
    })
    metadata = {
        "method": "per_time_float32_tail_clear_then_uint32_3d_delta",
        "shape": list(analysis.shape), "input": str(args.input.resolve()),
        "tails": {"below_7bit": tails_below, "above_6bit": tails_above},
        "tail_validation": {"bit_exact": True, "exact_256_count": exact_256},
        "elapsed_seconds": time.perf_counter() - started,
    }
    result = save_bundle(args.output, metadata, distributions)
    view = {"distributions": {k: result["distributions"][f"cut_{k}"] for k in base}}
    result["weighted_delta_entropy"] = weighted_delta_entropy(view)
    (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(args.report, result)
    plot_results(result, args.figures)
    print(f"Wrote {args.output / 'results.json'}")
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
