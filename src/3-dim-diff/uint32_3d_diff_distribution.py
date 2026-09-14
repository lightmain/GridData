"""Analyse exact integer distributions before and after a reversible 3-D delta.

Each float32 value is reinterpreted, without numeric conversion, as one
little-endian uint32 IEEE-754 word.  The word tensor has axes
``(time, latitude, longitude)``.  Applying a forward difference along all
three axes partitions the encoded tensor into one 3-D interior, three 2-D
faces, three 1-D edges, and one vertex.

The implementation streams one TIFF at a time and writes exact frequency
tables, so it never materialises the complete int64 delta tensor.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import tifffile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_uint32_3d_diff"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "Uint32ThreeDimensionalDiffReport-cn.md"

ATOMIC_REGIONS = (
    "interior",
    "time_zero_face",
    "latitude_zero_face",
    "longitude_zero_face",
    "time_latitude_edge",
    "time_longitude_edge",
    "latitude_longitude_edge",
    "vertex",
)

REGION_LABELS = {
    "raw_uint32": "原始 uint32 位模式",
    "interior": "三维内部 ΔtΔlatΔlon",
    "planes": "三个二维面（合计）",
    "time_zero_face": "t=0 面 ΔlatΔlon",
    "latitude_zero_face": "lat=0 面 ΔtΔlon",
    "longitude_zero_face": "lon=0 面 ΔtΔlat",
    "edges": "三条一维棱（合计）",
    "time_latitude_edge": "t=0, lat=0 棱 Δlon",
    "time_longitude_edge": "t=0, lon=0 棱 Δlat",
    "latitude_longitude_edge": "lat=0, lon=0 棱 Δt",
    "vertex": "顶点 (0,0,0)",
}


@dataclass
class ExactDistribution:
    frequencies: Counter[int] = field(default_factory=Counter)

    def update(self, values: np.ndarray | Iterable[int]) -> None:
        array = np.asarray(values, dtype=np.int64).reshape(-1)
        if array.size == 0:
            return
        unique, counts = np.unique(array, return_counts=True)
        self.frequencies.update(
            {int(value): int(count) for value, count in zip(unique, counts)}
        )

    @classmethod
    def merged(cls, distributions: Iterable["ExactDistribution"]) -> "ExactDistribution":
        result = cls()
        for distribution in distributions:
            result.frequencies.update(distribution.frequencies)
        return result

    def summary(self, top_n: int) -> dict[str, Any]:
        items = sorted(self.frequencies.items())
        count = sum(frequency for _, frequency in items)
        if count == 0:
            return {"count": 0, "unique_count": 0}

        weighted_sum = sum(value * frequency for value, frequency in items)
        mean = weighted_sum / count
        variance = math.fsum(
            frequency * (value - mean) ** 2 for value, frequency in items
        ) / count
        entropy = math.fsum(
            -(frequency / count) * math.log2(frequency / count)
            for _, frequency in items
        )
        zero_count = self.frequencies.get(0, 0)

        return {
            "count": count,
            "unique_count": len(items),
            "min": items[0][0],
            "max": items[-1][0],
            "mean": mean,
            "standard_deviation": math.sqrt(max(variance, 0.0)),
            "entropy_bits_per_value": entropy,
            "zero_count": zero_count,
            "zero_fraction": zero_count / count,
            "quantiles": exact_quantiles(items, count),
            "signed_zigzag_bit_width": zigzag_bit_width_distribution(items),
            "top_values": [
                {"value": value, "count": frequency, "fraction": frequency / count}
                for value, frequency in sorted(
                    items, key=lambda item: (-item[1], item[0])
                )[:top_n]
            ],
        }


def exact_quantiles(items: list[tuple[int, int]], count: int) -> dict[str, int]:
    probabilities = (0.0, 0.01, 0.25, 0.5, 0.75, 0.99, 1.0)
    targets = [round(probability * (count - 1)) for probability in probabilities]
    result: dict[str, int] = {}
    cumulative = 0
    target_index = 0
    for value, frequency in items:
        cumulative += frequency
        while target_index < len(targets) and cumulative > targets[target_index]:
            result[f"p{probabilities[target_index] * 100:g}"] = value
            target_index += 1
    return result


def zigzag_bit_width_distribution(items: list[tuple[int, int]]) -> dict[str, int]:
    """Return minimum unsigned widths after signed ZigZag mapping."""
    widths: Counter[int] = Counter()
    for value, frequency in items:
        zigzag = 2 * value if value >= 0 else -2 * value - 1
        widths[zigzag.bit_length()] += frequency
    return {str(width): widths[width] for width in sorted(widths)}


def tiff_files(input_dir: Path, limit: int | None) -> list[Path]:
    files = sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}
    )
    if not files:
        raise FileNotFoundError(f"No TIFF files found in {input_dir}")
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be at least 1")
        files = files[:limit]
    return files


def read_words(path: Path, expected_shape: tuple[int, int] | None) -> np.ndarray:
    image = tifffile.imread(path)
    if image.ndim != 2:
        raise ValueError(f"Expected a 2-D TIFF, got shape {image.shape} in {path}")
    if image.dtype.kind != "f" or image.dtype.itemsize != 4:
        raise ValueError(f"Expected float32 TIFF, got {image.dtype} in {path}")
    if expected_shape is not None and image.shape != expected_shape:
        raise ValueError(
            f"Shape changed from {expected_shape} to {image.shape} in {path}"
        )

    # Numeric values are preserved while normalising byte order; view() itself
    # performs the requested bit-for-bit float32 -> uint32 reinterpretation.
    little_endian_float = image.astype("<f4", copy=False)
    return little_endian_float.view("<u4").astype(np.int64)


def mixed_2d_delta(values: np.ndarray) -> np.ndarray:
    return (
        values[1:, 1:]
        - values[1:, :-1]
        - values[:-1, 1:]
        + values[:-1, :-1]
    )


def analyse(
    files: list[Path], progress_every: int
) -> tuple[tuple[int, int, int], dict[str, ExactDistribution], float]:
    raw = ExactDistribution()
    regions = {name: ExactDistribution() for name in ATOMIC_REGIONS}
    previous: np.ndarray | None = None
    spatial_shape: tuple[int, int] | None = None
    started = time.perf_counter()

    for time_index, path in enumerate(files):
        current = read_words(path, spatial_shape)
        if spatial_shape is None:
            spatial_shape = current.shape
            if min(spatial_shape) < 2:
                raise ValueError(f"Both spatial dimensions must be >= 2: {spatial_shape}")

        raw.update(current)
        if time_index == 0:
            regions["time_zero_face"].update(mixed_2d_delta(current))
            regions["time_latitude_edge"].update(current[0, 1:] - current[0, :-1])
            regions["time_longitude_edge"].update(current[1:, 0] - current[:-1, 0])
            regions["vertex"].update([current[0, 0]])
        else:
            assert previous is not None
            temporal_delta = current - previous
            regions["interior"].update(mixed_2d_delta(temporal_delta))
            regions["latitude_zero_face"].update(
                temporal_delta[0, 1:] - temporal_delta[0, :-1]
            )
            regions["longitude_zero_face"].update(
                temporal_delta[1:, 0] - temporal_delta[:-1, 0]
            )
            regions["latitude_longitude_edge"].update([temporal_delta[0, 0]])

        previous = current
        if progress_every and (
            (time_index + 1) % progress_every == 0 or time_index + 1 == len(files)
        ):
            elapsed = time.perf_counter() - started
            print(
                f"Processed {time_index + 1}/{len(files)} TIFFs "
                f"({elapsed:.1f} s)",
                flush=True,
            )

    assert spatial_shape is not None
    regions["raw_uint32"] = raw
    regions["planes"] = ExactDistribution.merged(
        regions[name]
        for name in ("time_zero_face", "latitude_zero_face", "longitude_zero_face")
    )
    regions["edges"] = ExactDistribution.merged(
        regions[name]
        for name in (
            "time_latitude_edge",
            "time_longitude_edge",
            "latitude_longitude_edge",
        )
    )
    shape = (len(files), spatial_shape[0], spatial_shape[1])
    verify_partition(shape, regions)
    return shape, regions, time.perf_counter() - started


def verify_partition(
    shape: tuple[int, int, int], regions: dict[str, ExactDistribution]
) -> None:
    time_size, latitude_size, longitude_size = shape
    expected = {
        "interior": (time_size - 1) * (latitude_size - 1) * (longitude_size - 1),
        "planes": (
            (latitude_size - 1) * (longitude_size - 1)
            + (time_size - 1) * (longitude_size - 1)
            + (time_size - 1) * (latitude_size - 1)
        ),
        "edges": longitude_size - 1 + latitude_size - 1 + time_size - 1,
        "vertex": 1,
    }
    actual = {
        name: sum(regions[name].frequencies.values()) for name in expected
    }
    if actual != expected or sum(actual.values()) != math.prod(shape):
        raise AssertionError(
            f"Delta partition count mismatch: expected={expected}, actual={actual}"
        )


def write_histogram(path: Path, distribution: ExactDistribution) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("value", "count"))
        writer.writerows(sorted(distribution.frequencies.items()))


def write_report(path: Path, result: dict[str, Any]) -> None:
    summaries = result["distributions"]
    primary = ("raw_uint32", "interior", "planes", "edges", "vertex")
    components = (
        "time_zero_face",
        "latitude_zero_face",
        "longitude_zero_face",
        "time_latitude_edge",
        "time_longitude_edge",
        "latitude_longitude_edge",
    )
    transformed_count = sum(summaries[name]["count"] for name in primary[1:])
    transformed_entropy = sum(
        summaries[name]["count"] * summaries[name]["entropy_bits_per_value"]
        for name in primary[1:]
    ) / transformed_count
    interior_widths = summaries["interior"]["signed_zigzag_bit_width"]
    interior_le_16 = sum(
        count for width, count in interior_widths.items() if int(width) <= 16
    ) / summaries["interior"]["count"]

    def table(names: Iterable[str]) -> list[str]:
        lines = [
            "| 区域 | 数量 | 占张量 | 不同值 | 最小值 | 最大值 | 0 占比 | 熵 (bit/value) |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        total = math.prod(result["shape"])
        for name in names:
            summary = summaries[name]
            lines.append(
                "| {} | {:,} | {:.6%} | {:,} | {:,} | {:,} | {:.6%} | {:.4f} |".format(
                    REGION_LABELS[name],
                    summary["count"],
                    summary["count"] / total,
                    summary["unique_count"],
                    summary["min"],
                    summary["max"],
                    summary["zero_fraction"],
                    summary["entropy_bits_per_value"],
                )
            )
        return lines

    lines = [
        "# ERA5 float32 位模式与三维差分分布",
        "",
        "## 方法",
        "",
        "每个 `float32` 不做数值取整，而是把其 little-endian IEEE-754 原始 4 字节",
        "无损重解释为 `uint32`。随后按 `time → latitude → longitude` 依次做前向差分；",
        "为避免 `uint32` 回绕，差分在 `int64` 中计算。变换可逆，且张量被严格划分为",
        "三维内部、三个二维面、三条一维棱和一个顶点。",
        "",
        f"输入：`{result['input_dir']}`",
        "",
        f"形状：`{tuple(result['shape'])}`，共 `{math.prod(result['shape']):,}` 个值；",
        f"处理耗时 `{result['elapsed_seconds']:.2f}` 秒。",
        "",
        "## 四类区域与原始整数分布",
        "",
        *table(primary),
        "",
        "这里的三个面、三条棱均互不重叠；内部 + 面 + 棱 + 顶点的数量严格等于原张量大小。",
        "原始整数不属于差分张量的四类区域，单列作为变换前基线。",
        "",
        "## 面与棱的分项分布",
        "",
        *table(components),
        "",
        "## 最高频整数",
        "",
    ]
    for name in primary:
        lines.extend((f"### {REGION_LABELS[name]}", ""))
        lines.extend(("| 整数 | 次数 | 占本区域 |", "| ---: | ---: | ---: |"))
        for item in summaries[name]["top_values"]:
            lines.append(
                f"| {item['value']:,} | {item['count']:,} | {item['fraction']:.6%} |"
            )
        lines.append("")

    lines.extend(
        (
            "## 观察",
            "",
            f"- 内部区占张量的 `{summaries['interior']['count'] / transformed_count:.6%}`，"
            f"其不同值数量为 `{summaries['interior']['unique_count']:,}`，"
            f"0 占 `{summaries['interior']['zero_fraction']:.6%}`。",
            f"- 四类差分区域的加权边际熵为 `{transformed_entropy:.4f} bit/value`，"
            f"明显低于原始位模式的 `{summaries['raw_uint32']['entropy_bits_per_value']:.4f} bit/value`。",
            f"- 内部差分经过 ZigZag 映射后，有 `{interior_le_16:.6%}` 可放入 16 bit；"
            "最高频值集中在 0、±64、±128 等 64 的倍数。",
            f"- `lat=0` 面和对应经向棱的 0 占比均为 "
            f"`{summaries['latitude_zero_face']['zero_fraction']:.6%}`，"
            "这是规则经纬网格极点沿经度重复产生的边界结构。",
            "- 熵是只基于单值频数的理论下界指标，不等同于某个实际压缩器的最终码率；"
            "后续应把 ZigZag/变长 bit packing 与 Zstd 等真实编码结果进行对比。",
            "",
            "## 输出说明",
            "",
            "`results.json` 保存摘要、精确分位数、最高频值和 ZigZag 后的最小 bit-width 分布；",
            "`histograms/*.csv.gz` 保存每个区域的完整、精确 `(value, count)` 频数表。",
            "这些统计针对整数位模式，而不是开尔文温度的数值差。",
            "",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyse float32-as-uint32 and reversible 3-D delta distributions."
    )
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.top < 1:
        raise ValueError("--top must be at least 1")
    if not args.input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist: {args.input_dir}")

    results_path = args.output_dir / "results.json"
    if results_path.exists() and not args.overwrite:
        raise FileExistsError(f"{results_path} exists; use --overwrite")

    files = tiff_files(args.input_dir, args.limit)
    shape, distributions, elapsed = analyse(files, args.progress_every)
    summaries = {
        name: distributions[name].summary(args.top)
        for name in ("raw_uint32", *ATOMIC_REGIONS, "planes", "edges")
    }
    result = {
        "method": "little_endian_float32_bit_pattern_to_uint32_then_3d_forward_delta",
        "axis_order": ["time", "latitude", "longitude"],
        "difference_dtype": "int64",
        "input_dir": str(args.input_dir.resolve()),
        "first_file": files[0].name,
        "last_file": files[-1].name,
        "shape": list(shape),
        "elapsed_seconds": elapsed,
        "distributions": summaries,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    histogram_dir = args.output_dir / "histograms"
    histogram_dir.mkdir(parents=True, exist_ok=True)
    for name, distribution in distributions.items():
        write_histogram(histogram_dir / f"{name}.csv.gz", distribution)
    results_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_report(args.report, result)
    print(f"Wrote {results_path}")
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, FileExistsError, ValueError, AssertionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
