"""Test and plot normality of interior 3-D deltas divisible by 64."""

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
DEFAULT_HISTOGRAM = (
    PROJECT_ROOT
    / "data"
    / "ERA5-temperature-May2026_uint32_3d_diff"
    / "histograms"
    / "interior.csv.gz"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "data"
    / "ERA5-temperature-May2026_uint32_3d_diff"
    / "multiple64_normality.json"
)
DEFAULT_FIGURE = (
    PROJECT_ROOT
    / "reports"
    / "figures"
    / "uint32_3d_diff"
    / "multiple64_normality_symlog_linear.png"
)


def read_multiple64_histogram(path: Path) -> tuple[np.ndarray, np.ndarray]:
    values: list[int] = []
    counts: list[int] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            value = int(row["value"])
            if value % 64 == 0:
                values.append(value // 64)
                counts.append(int(row["count"]))
    return np.asarray(values, dtype=np.int64), np.asarray(counts, dtype=np.int64)


def normal_cdf(values: np.ndarray, mean: float, standard_deviation: float) -> np.ndarray:
    scale = standard_deviation * math.sqrt(2.0)
    return np.fromiter(
        (0.5 * (1.0 + math.erf((float(value) - mean) / scale)) for value in values),
        dtype=np.float64,
        count=values.size,
    )


def weighted_quantiles(
    values: np.ndarray, counts: np.ndarray, probabilities: tuple[float, ...]
) -> dict[str, float]:
    cumulative = np.cumsum(counts, dtype=np.int64)
    targets = np.asarray([round(p * (int(cumulative[-1]) - 1)) for p in probabilities])
    indices = np.searchsorted(cumulative, targets, side="right")
    return {
        f"p{100 * probability:g}": float(values[index])
        for probability, index in zip(probabilities, indices)
    }


def analyse(values: np.ndarray, counts: np.ndarray) -> tuple[dict[str, Any], np.ndarray, np.ndarray]:
    sample_count = int(counts.sum())
    weights = counts.astype(np.float64)
    numeric_values = values.astype(np.float64)
    mean = float(np.dot(numeric_values, weights) / sample_count)
    centered = numeric_values - mean
    variance = float(np.dot(centered * centered, weights) / sample_count)
    standard_deviation = math.sqrt(variance)
    skewness = float(
        np.dot(centered**3, weights) / sample_count / standard_deviation**3
    )
    excess_kurtosis = float(
        np.dot(centered**4, weights) / sample_count / standard_deviation**4 - 3.0
    )

    full_values = np.arange(int(values[0]), int(values[-1]) + 1, dtype=np.int64)
    empirical_mass = np.zeros(full_values.size, dtype=np.float64)
    empirical_mass[values - full_values[0]] = weights / sample_count
    lower_edges = full_values.astype(np.float64) - 0.5
    upper_edges = full_values.astype(np.float64) + 0.5
    normal_lower = normal_cdf(lower_edges, mean, standard_deviation)
    normal_upper = normal_cdf(upper_edges, mean, standard_deviation)
    normal_mass = normal_upper - normal_lower

    empirical_upper = np.cumsum(empirical_mass)
    empirical_lower = empirical_upper - empirical_mass
    discrete_ks = float(
        max(
            np.max(np.abs(empirical_upper - normal_upper)),
            np.max(np.abs(empirical_lower - normal_lower)),
        )
    )
    normal_tail_mass = float(normal_lower[0] + 1.0 - normal_upper[-1])
    total_variation = float(
        0.5 * (np.sum(np.abs(empirical_mass - normal_mass)) + normal_tail_mass)
    )

    zero_index = -full_values[0]
    zero_mass = float(empirical_mass[zero_index]) if 0 <= zero_index < full_values.size else 0.0
    fitted_zero_mass = float(normal_mass[zero_index]) if 0 <= zero_index < full_values.size else 0.0
    even_count = float(weights[values % 2 == 0].sum())
    zero_count = float(weights[values == 0].sum())
    sigma_coverage = {
        str(sigma): float(weights[np.abs(centered) <= sigma * standard_deviation].sum() / sample_count)
        for sigma in (1, 2, 3)
    }
    nonzero_mask = values != 0
    nonzero_values = numeric_values[nonzero_mask]
    nonzero_weights = weights[nonzero_mask]
    nonzero_count = float(nonzero_weights.sum())
    nonzero_mean = float(np.dot(nonzero_values, nonzero_weights) / nonzero_count)
    nonzero_centered = nonzero_values - nonzero_mean
    nonzero_variance = float(
        np.dot(nonzero_centered * nonzero_centered, nonzero_weights) / nonzero_count
    )
    nonzero_standard_deviation = math.sqrt(nonzero_variance)
    nonzero_excess_kurtosis = float(
        np.dot(nonzero_centered**4, nonzero_weights)
        / nonzero_count
        / nonzero_standard_deviation**4
        - 3.0
    )
    nonzero_sigma_coverage = {
        str(sigma): float(
            nonzero_weights[
                np.abs(nonzero_centered) <= sigma * nonzero_standard_deviation
            ].sum()
            / nonzero_count
        )
        for sigma in (1, 2, 3)
    }
    magnitude_counts = np.zeros(int(np.max(np.abs(values))) + 1, dtype=np.float64)
    np.add.at(magnitude_counts, np.abs(values), weights)
    magnitudes = np.arange(1, magnitude_counts.size, dtype=np.float64)
    magnitude_weights = magnitude_counts[1:]
    log_magnitudes = np.log(magnitudes)
    log_mean = float(np.dot(log_magnitudes, magnitude_weights) / nonzero_count)
    log_centered = log_magnitudes - log_mean
    log_variance = float(
        np.dot(log_centered * log_centered, magnitude_weights) / nonzero_count
    )
    log_standard_deviation = math.sqrt(log_variance)
    log_skewness = float(
        np.dot(log_centered**3, magnitude_weights)
        / nonzero_count
        / log_standard_deviation**3
    )
    log_excess_kurtosis = float(
        np.dot(log_centered**4, magnitude_weights)
        / nonzero_count
        / log_standard_deviation**4
        - 3.0
    )
    magnitude_empirical_mass = magnitude_weights / nonzero_count
    magnitude_lower = np.maximum(magnitudes - 0.5, 0.5)
    magnitude_upper = magnitudes + 0.5
    lognormal_lower = normal_cdf(
        np.log(magnitude_lower), log_mean, log_standard_deviation
    )
    lognormal_upper = normal_cdf(
        np.log(magnitude_upper), log_mean, log_standard_deviation
    )
    lognormal_mass = lognormal_upper - lognormal_lower
    magnitude_empirical_upper = np.cumsum(magnitude_empirical_mass)
    magnitude_empirical_lower = magnitude_empirical_upper - magnitude_empirical_mass
    lognormal_ks = float(
        max(
            np.max(np.abs(magnitude_empirical_upper - lognormal_upper)),
            np.max(np.abs(magnitude_empirical_lower - lognormal_lower)),
        )
    )
    lognormal_tail_mass = float(
        lognormal_lower[0] + 1.0 - lognormal_upper[-1]
    )
    lognormal_total_variation = float(
        0.5
        * (
            np.sum(np.abs(magnitude_empirical_mass - lognormal_mass))
            + lognormal_tail_mass
        )
    )
    quantiles = weighted_quantiles(
        numeric_values,
        counts,
        (0.001, 0.01, 0.025, 0.25, 0.5, 0.75, 0.975, 0.99, 0.999),
    )

    result = {
        "selection": "interior delta values exactly divisible by 64, including zero",
        "scaled_variable": "q = delta / 64",
        "sample_count": sample_count,
        "excluded_non64_count": 1_709_778,
        "mean_q": mean,
        "standard_deviation_q": standard_deviation,
        "mean_delta": mean * 64,
        "standard_deviation_delta": standard_deviation * 64,
        "skewness": skewness,
        "excess_kurtosis": excess_kurtosis,
        "empirical_zero_fraction": zero_mass,
        "fitted_normal_zero_bin_fraction": fitted_zero_mass,
        "zero_peak_ratio_to_fitted_normal": zero_mass / fitted_zero_mass,
        "even_q_fraction": even_count / sample_count,
        "even_q_fraction_excluding_zero": (even_count - zero_count)
        / (sample_count - zero_count),
        "discretized_normal_ks_distance": discrete_ks,
        "total_variation_distance_to_discretized_normal": total_variation,
        "coverage_within_fitted_sigma": sigma_coverage,
        "robustness_check_excluding_zero": {
            "sample_count": int(nonzero_count),
            "mean_q": nonzero_mean,
            "standard_deviation_q": nonzero_standard_deviation,
            "excess_kurtosis": nonzero_excess_kurtosis,
            "coverage_within_fitted_sigma": nonzero_sigma_coverage,
        },
        "lognormal_check_for_nonzero_magnitude": {
            "variable": "abs(q), conditional on q != 0",
            "sample_count": int(nonzero_count),
            "positive_sign_fraction": float(weights[values > 0].sum() / nonzero_count),
            "negative_sign_fraction": float(weights[values < 0].sum() / nonzero_count),
            "log_mean": log_mean,
            "log_standard_deviation": log_standard_deviation,
            "log_skewness": log_skewness,
            "log_excess_kurtosis": log_excess_kurtosis,
            "discretized_lognormal_ks_distance": lognormal_ks,
            "total_variation_distance_to_discretized_lognormal": lognormal_total_variation,
            "conclusion": "moderate_engineering_approximation_but_not_lognormal",
        },
        "normal_reference_coverage": {
            "1": 0.682689492,
            "2": 0.954499736,
            "3": 0.997300204,
        },
        "quantiles_q": quantiles,
        "conclusion": "not_normal: sharp zero spike, narrow centre, and heavy tails",
    }
    return result, full_values * 64, np.vstack((empirical_mass, normal_mass))


def configure_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
            "axes.unicode_minus": False,
            "axes.titleweight": "bold",
            "savefig.dpi": 180,
            "savefig.bbox": "tight",
        }
    )


def plot(result: dict[str, Any], delta_values: np.ndarray, masses: np.ndarray, path: Path) -> None:
    configure_style()
    empirical_mass, normal_mass = masses
    figure, axis = plt.subplots(figsize=(14, 6.8), constrained_layout=True)
    axis.plot(
        delta_values,
        empirical_mass,
        color="#2563eb",
        linewidth=1.7,
        label="实际精确频率（仅 64 倍数）",
        zorder=3,
    )
    axis.plot(
        delta_values,
        normal_mass,
        color="#dc2626",
        linewidth=2.0,
        linestyle="--",
        label="相同均值/方差的离散化正态分布",
        zorder=2,
    )
    axis.scatter(
        [0],
        [result["empirical_zero_fraction"]],
        color="#7c3aed",
        s=55,
        zorder=4,
    )
    axis.set_xscale("symlog", linthresh=1024, linscale=1.0)
    axis.set_xticks((-500_000, -100_000, -10_000, -1_000, 0, 1_000, 10_000, 100_000, 500_000))
    axis.set_ylim(bottom=0)
    axis.yaxis.set_major_formatter(PercentFormatter(1.0))
    axis.set_title("内部差分排除非 64 倍数后的分布：并不符合正态分布", fontsize=16)
    axis.set_xlabel("差分整数（symlog；±1024 内为线性，外侧为对数压缩）")
    axis.set_ylabel("占筛选后内部值的比例（线性坐标）")
    axis.legend(loc="upper right")
    metrics = (
        f"样本数：{result['sample_count']:,}\n"
        f"0 的实际频率：{result['empirical_zero_fraction']:.3%}\n"
        f"正态模型 0-bin：{result['fitted_normal_zero_bin_fraction']:.3%}\n"
        f"偏度：{result['skewness']:.3f}\n"
        f"超额峰度：{result['excess_kurtosis']:.1f}\n"
        f"离散 KS 距离：{result['discretized_normal_ks_distance']:.3f}"
    )
    axis.text(
        0.02,
        0.96,
        metrics,
        transform=axis.transAxes,
        va="top",
        bbox={"boxstyle": "round,pad=0.5", "facecolor": "white", "alpha": 0.9},
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, facecolor="white")
    plt.close(figure)
    print(f"Wrote {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--histogram", type=Path, default=DEFAULT_HISTOGRAM)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    values, counts = read_multiple64_histogram(args.histogram)
    result, delta_values, masses = analyse(values, counts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    plot(result, delta_values, masses, args.figure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
