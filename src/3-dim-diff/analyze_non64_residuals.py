"""Locate and explain interior 3-D uint32 deltas not divisible by 64."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import PercentFormatter
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "data"
    / "ERA5-temperature-May2026_uint32_3d_diff"
    / "non64_diagnostics.json"
)
DEFAULT_FIGURE = (
    PROJECT_ROOT / "reports" / "figures" / "uint32_3d_diff" / "non64_diagnostics.png"
)
EXPONENT_BOUNDARY = 256.0


def read_float32(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        values = np.asarray(image)
    if values.ndim != 2 or values.dtype != np.dtype("float32"):
        raise ValueError(f"Expected a 2-D float32 TIFF, got {values.shape} {values.dtype}")
    return values


def mixed_2d_delta(values: np.ndarray) -> np.ndarray:
    return (
        values[1:, 1:]
        - values[1:, :-1]
        - values[:-1, 1:]
        + values[:-1, :-1]
    )


def spatial_crosses_boundary(values: np.ndarray) -> np.ndarray:
    low = values < EXPONENT_BOUNDARY
    low00 = low[:-1, :-1]
    low01 = low[:-1, 1:]
    low10 = low[1:, :-1]
    low11 = low[1:, 1:]
    any_low = low00 | low01 | low10 | low11
    all_low = low00 & low01 & low10 & low11
    return any_low & ~all_low


def stencil_geometry(
    previous: np.ndarray, current: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    corners = (
        previous[:-1, :-1],
        previous[:-1, 1:],
        previous[1:, :-1],
        previous[1:, 1:],
        current[:-1, :-1],
        current[:-1, 1:],
        current[1:, :-1],
        current[1:, 1:],
    )
    minimum = np.minimum.reduce(corners)
    maximum = np.maximum.reduce(corners)
    nearest = np.minimum.reduce(
        tuple(np.abs(corner - EXPONENT_BOUNDARY) for corner in corners)
    )
    return minimum, maximum, nearest


def percentile_summary(values: np.ndarray) -> dict[str, float]:
    probabilities = (0, 1, 25, 50, 75, 99, 100)
    quantiles = np.percentile(values, probabilities)
    return {f"p{probability}": float(value) for probability, value in zip(probabilities, quantiles)}


def analyse(files: list[Path], progress_every: int) -> dict[str, Any]:
    previous = read_float32(files[0])
    height, width = previous.shape
    latitude_bad = np.zeros(height - 1, dtype=np.int64)
    latitude_cross = np.zeros(height - 1, dtype=np.int64)
    time_bad = np.zeros(len(files) - 1, dtype=np.int64)
    time_cross = np.zeros(len(files) - 1, dtype=np.int64)
    category_total: Counter[str] = Counter()
    category_bad: Counter[str] = Counter()
    bad_residues: Counter[int] = Counter()
    nearest_chunks: list[np.ndarray] = []
    span_chunks: list[np.ndarray] = []
    examples: list[dict[str, Any]] = []

    for time_index, path in enumerate(files[1:], start=1):
        current = read_float32(path)
        if current.shape != previous.shape:
            raise ValueError(f"Shape changed in {path}: {current.shape}")

        previous_words = previous.view("<u4").astype(np.int64)
        current_words = current.view("<u4").astype(np.int64)
        delta = mixed_2d_delta(current_words - previous_words)
        bad = delta % 64 != 0

        previous_cross = spatial_crosses_boundary(previous)
        current_cross = spatial_crosses_boundary(current)
        spatial_cross = previous_cross | current_cross
        minimum, maximum, nearest = stencil_geometry(previous, current)
        any_eight_point_cross = (minimum < EXPONENT_BOUNDARY) & (
            maximum >= EXPONENT_BOUNDARY
        )
        temporal_only_cross = any_eight_point_cross & ~spatial_cross

        categories = {
            "neither_spatial_face_crosses": ~previous_cross & ~current_cross,
            "previous_face_only": previous_cross & ~current_cross,
            "current_face_only": ~previous_cross & current_cross,
            "both_spatial_faces_cross": previous_cross & current_cross,
            "any_spatial_face_crosses": spatial_cross,
            "eight_point_crosses": any_eight_point_cross,
            "temporal_only_crosses": temporal_only_cross,
        }
        for name, mask in categories.items():
            category_total[name] += int(np.count_nonzero(mask))
            category_bad[name] += int(np.count_nonzero(mask & bad))

        bad_count = int(np.count_nonzero(bad))
        cross_count = int(np.count_nonzero(spatial_cross))
        time_bad[time_index - 1] = bad_count
        time_cross[time_index - 1] = cross_count
        latitude_bad += np.count_nonzero(bad, axis=1)
        latitude_cross += np.count_nonzero(spatial_cross, axis=1)

        bad_values = delta[bad]
        residues, residue_counts = np.unique(bad_values % 64, return_counts=True)
        bad_residues.update(
            {int(value): int(count) for value, count in zip(residues, residue_counts)}
        )
        if bad_count:
            nearest_chunks.append(nearest[bad])
            span_chunks.append(maximum[bad] - minimum[bad])

        if len(examples) < 8 and bad_count:
            ys, xs = np.nonzero(bad)
            for y, x in zip(ys, xs):
                examples.append(
                    {
                        "time_index": time_index,
                        "previous_file": files[time_index - 1].name,
                        "current_file": path.name,
                        "latitude_midpoint_degrees_north": 90.0 - (y + 0.5) * 0.25,
                        "longitude_midpoint_degrees_east": (x + 0.5) * 0.25,
                        "delta": int(delta[y, x]),
                        "delta_mod_64": int(delta[y, x] % 64),
                        "stencil_temperatures_k": [
                            float(previous[y, x]),
                            float(previous[y, x + 1]),
                            float(previous[y + 1, x]),
                            float(previous[y + 1, x + 1]),
                            float(current[y, x]),
                            float(current[y, x + 1]),
                            float(current[y + 1, x]),
                            float(current[y + 1, x + 1]),
                        ],
                    }
                )
                if len(examples) == 8:
                    break

        previous = current
        if progress_every and (time_index % progress_every == 0 or time_index == len(files) - 1):
            print(f"Processed temporal pair {time_index}/{len(files) - 1}", flush=True)

    total = (len(files) - 1) * (height - 1) * (width - 1)
    bad_total = int(time_bad.sum())
    spatial_cross_total = int(time_cross.sum())
    nearest = np.concatenate(nearest_chunks)
    spans = np.concatenate(span_chunks)
    latitude_midpoints = 90.0 - (np.arange(height - 1) + 0.5) * 0.25
    top_latitude_indices = np.argsort(latitude_bad)[-20:][::-1]
    top_time_indices = np.argsort(time_bad)[-20:][::-1]

    return {
        "shape": [len(files), height, width],
        "stencil_shape": [2, 2, 2],
        "float32_exponent_boundary_k": EXPONENT_BOUNDARY,
        "total_interior_stencils": total,
        "non_multiple_of_64_count": bad_total,
        "non_multiple_of_64_fraction": bad_total / total,
        "spatial_boundary_crossing_count": spatial_cross_total,
        "spatial_boundary_crossing_fraction": spatial_cross_total / total,
        "non64_given_spatial_crossing": category_bad["any_spatial_face_crosses"]
        / spatial_cross_total,
        "non64_outside_spatial_crossing": bad_total
        - category_bad["any_spatial_face_crosses"],
        "categories": {
            name: {
                "count": category_total[name],
                "fraction": category_total[name] / total,
                "non64_count": category_bad[name],
                "non64_fraction_within_category": (
                    category_bad[name] / category_total[name]
                    if category_total[name]
                    else 0.0
                ),
            }
            for name in category_total
        },
        "bad_residues_mod_64": {
            str(value): count for value, count in sorted(bad_residues.items())
        },
        "nearest_corner_distance_to_256_k": percentile_summary(nearest),
        "stencil_temperature_span_k": percentile_summary(spans),
        "top_latitude_bands": [
            {
                "latitude_midpoint_degrees_north": float(latitude_midpoints[index]),
                "non64_count": int(latitude_bad[index]),
                "non64_fraction": float(latitude_bad[index] / ((len(files) - 1) * (width - 1))),
            }
            for index in top_latitude_indices
        ],
        "top_temporal_pairs": [
            {
                "previous_file": files[index].name,
                "current_file": files[index + 1].name,
                "non64_count": int(time_bad[index]),
                "non64_fraction": float(time_bad[index] / ((height - 1) * (width - 1))),
            }
            for index in top_time_indices
        ],
        "latitude_midpoints_degrees_north": latitude_midpoints.tolist(),
        "latitude_non64_count": latitude_bad.tolist(),
        "latitude_spatial_crossing_count": latitude_cross.tolist(),
        "time_non64_count": time_bad.tolist(),
        "time_spatial_crossing_count": time_cross.tolist(),
        "examples": examples,
    }


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


def plot_diagnostics(result: dict[str, Any], path: Path) -> None:
    configure_style()
    figure, axes = plt.subplots(2, 2, figsize=(14, 8.5), constrained_layout=True)
    figure.suptitle("非 64 倍数内部差分的来源诊断", fontsize=17)

    latitudes = np.asarray(result["latitude_midpoints_degrees_north"])
    latitude_bad = np.asarray(result["latitude_non64_count"])
    latitude_denominator = (result["shape"][0] - 1) * (result["shape"][2] - 1)
    axes[0, 0].plot(latitudes, latitude_bad / latitude_denominator, color="#2563eb")
    axes[0, 0].set_title("非 64 倍数随纬度的比例")
    axes[0, 0].set_xlabel("stencil 纬度中点（°N）")
    axes[0, 0].set_ylabel("占该纬度所有 stencil")
    axes[0, 0].yaxis.set_major_formatter(PercentFormatter(1.0))
    axes[0, 0].invert_xaxis()

    time_bad = np.asarray(result["time_non64_count"])
    time_denominator = (result["shape"][1] - 1) * (result["shape"][2] - 1)
    axes[0, 1].plot(np.arange(1, len(time_bad) + 1), time_bad / time_denominator, color="#0f766e")
    axes[0, 1].set_title("非 64 倍数随时间对的比例")
    axes[0, 1].set_xlabel("相邻小时对索引")
    axes[0, 1].set_ylabel("占该时间对所有 stencil")
    axes[0, 1].yaxis.set_major_formatter(PercentFormatter(1.0))

    names = ("neither_spatial_face_crosses", "previous_face_only", "current_face_only", "both_spatial_faces_cross")
    labels = ("两面均不跨越", "仅前一小时跨越", "仅当前小时跨越", "两小时均跨越")
    rates = [result["categories"][name]["non64_fraction_within_category"] for name in names]
    bars = axes[1, 0].bar(labels, rates, color=["#94a3b8", "#60a5fa", "#2563eb", "#7c3aed"])
    axes[1, 0].set_title("按 2×2 空间面是否跨越 256 K 分类")
    axes[1, 0].set_ylabel("该类中非 64 倍数比例")
    axes[1, 0].yaxis.set_major_formatter(PercentFormatter(1.0))
    axes[1, 0].tick_params(axis="x", rotation=12)
    axes[1, 0].bar_label(bars, labels=[f"{rate:.2%}" for rate in rates], padding=3)

    residue_counts = np.zeros(64, dtype=np.int64)
    for residue, count in result["bad_residues_mod_64"].items():
        residue_counts[int(residue)] = count
    axes[1, 1].bar(np.arange(64), residue_counts / residue_counts.sum(), width=0.9, color="#d97706")
    axes[1, 1].set_title("例外值的余数分布（delta mod 64）")
    axes[1, 1].set_xlabel("余数")
    axes[1, 1].set_ylabel("占所有非 64 倍数")
    axes[1, 1].yaxis.set_major_formatter(PercentFormatter(1.0))

    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, facecolor="white")
    plt.close(figure)
    print(f"Wrote {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--figure", type=Path, default=DEFAULT_FIGURE)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=50)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = sorted(args.input_dir.glob("*.tiff"))
    if args.limit is not None:
        files = files[: args.limit]
    if len(files) < 2:
        raise ValueError("At least two TIFF files are required")
    result = analyse(files, args.progress_every)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    plot_diagnostics(result, args.figure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
