"""Shared exact-distribution helpers for streamed three-dimensional deltas."""

from __future__ import annotations

import csv
import gzip
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np


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


@dataclass
class ExactDistribution:
    frequencies: Counter[int] = field(default_factory=Counter)

    def update(self, values: np.ndarray | Iterable[int]) -> None:
        array = np.asarray(values, dtype=np.int64).reshape(-1)
        if not array.size:
            return
        values_, counts = np.unique(array, return_counts=True)
        self.frequencies.update(
            {int(v): int(c) for v, c in zip(values_, counts, strict=True)}
        )

    @classmethod
    def merged(cls, distributions: Iterable["ExactDistribution"]) -> "ExactDistribution":
        result = cls()
        for distribution in distributions:
            result.frequencies.update(distribution.frequencies)
        return result

    def summary(self, top_n: int = 20) -> dict[str, Any]:
        items = sorted(self.frequencies.items())
        count = sum(c for _, c in items)
        if not count:
            return {"count": 0, "unique_count": 0}
        mean = sum(v * c for v, c in items) / count
        variance = math.fsum(c * (v - mean) ** 2 for v, c in items) / count
        entropy = math.fsum(
            -(c / count) * math.log2(c / count) for _, c in items
        )
        widths: Counter[int] = Counter()
        for value, frequency in items:
            zigzag = 2 * value if value >= 0 else -2 * value - 1
            widths[zigzag.bit_length()] += frequency
        targets = {
            name: round(p * (count - 1))
            for name, p in (
                ("p0", 0), ("p1", .01), ("p25", .25), ("p50", .5),
                ("p75", .75), ("p99", .99), ("p100", 1),
            )
        }
        quantiles: dict[str, int] = {}
        cumulative = 0
        for value, frequency in items:
            cumulative += frequency
            for name, target in targets.items():
                if name not in quantiles and cumulative > target:
                    quantiles[name] = value
        return {
            "count": count,
            "unique_count": len(items),
            "min": items[0][0],
            "max": items[-1][0],
            "mean": mean,
            "standard_deviation": math.sqrt(max(variance, 0)),
            "entropy_bits_per_value": entropy,
            "zero_count": self.frequencies.get(0, 0),
            "zero_fraction": self.frequencies.get(0, 0) / count,
            "multiple_of_64_fraction": sum(c for v, c in items if v % 64 == 0) / count,
            "multiple_of_128_fraction": sum(c for v, c in items if v % 128 == 0) / count,
            "quantiles": quantiles,
            "signed_zigzag_bit_width": {
                str(w): widths[w] for w in sorted(widths)
            },
            "top_values": [
                {"value": v, "count": c, "fraction": c / count}
                for v, c in sorted(items, key=lambda x: (-x[1], x[0]))[:top_n]
            ],
        }


def mixed_2d_delta(values: np.ndarray) -> np.ndarray:
    return values[1:, 1:] - values[1:, :-1] - values[:-1, 1:] + values[:-1, :-1]


class StreamedThreeDimensionalDelta:
    def __init__(self) -> None:
        self.raw = ExactDistribution()
        self.regions = {name: ExactDistribution() for name in ATOMIC_REGIONS}
        self.previous: np.ndarray | None = None
        self.frames = 0
        self.spatial_shape: tuple[int, int] | None = None

    def update(self, frame: np.ndarray) -> np.ndarray | None:
        current = np.asarray(frame, dtype=np.int64)
        if current.ndim != 2:
            raise ValueError(f"Expected 2-D frame, got {current.shape}")
        if self.spatial_shape is None:
            self.spatial_shape = current.shape
        elif current.shape != self.spatial_shape:
            raise ValueError(f"Shape changed: {self.spatial_shape} -> {current.shape}")
        self.raw.update(current)
        temporal: np.ndarray | None = None
        if self.frames == 0:
            self.regions["time_zero_face"].update(mixed_2d_delta(current))
            self.regions["time_latitude_edge"].update(current[0, 1:] - current[0, :-1])
            self.regions["time_longitude_edge"].update(current[1:, 0] - current[:-1, 0])
            self.regions["vertex"].update([current[0, 0]])
        else:
            assert self.previous is not None
            temporal = current - self.previous
            self.regions["interior"].update(mixed_2d_delta(temporal))
            self.regions["latitude_zero_face"].update(temporal[0, 1:] - temporal[0, :-1])
            self.regions["longitude_zero_face"].update(temporal[1:, 0] - temporal[:-1, 0])
            self.regions["latitude_longitude_edge"].update([temporal[0, 0]])
        self.previous = current
        self.frames += 1
        return temporal

    def distributions(self) -> dict[str, ExactDistribution]:
        if self.spatial_shape is None:
            raise ValueError("No frames processed")
        result = {"raw": self.raw, **self.regions}
        result["planes"] = ExactDistribution.merged(
            self.regions[n] for n in (
                "time_zero_face", "latitude_zero_face", "longitude_zero_face"
            )
        )
        result["edges"] = ExactDistribution.merged(
            self.regions[n] for n in (
                "time_latitude_edge", "time_longitude_edge", "latitude_longitude_edge"
            )
        )
        verify_partition(self.shape, result)
        return result

    @property
    def shape(self) -> tuple[int, int, int]:
        if self.spatial_shape is None:
            raise ValueError("No frames processed")
        return (self.frames, *self.spatial_shape)


def verify_partition(shape: tuple[int, int, int], regions: dict[str, ExactDistribution]) -> None:
    t, y, x = shape
    expected = {
        "interior": (t - 1) * (y - 1) * (x - 1),
        "planes": (y - 1) * (x - 1) + (t - 1) * (x - 1) + (t - 1) * (y - 1),
        "edges": x - 1 + y - 1 + t - 1,
        "vertex": 1,
    }
    actual = {name: sum(regions[name].frequencies.values()) for name in expected}
    if actual != expected or sum(actual.values()) != math.prod(shape):
        raise AssertionError(f"Partition mismatch: expected={expected}, actual={actual}")


def save_bundle(
    output_dir: Path,
    metadata: dict[str, Any],
    distributions: dict[str, ExactDistribution],
    top_n: int = 20,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    histogram_dir = output_dir / "histograms"
    histogram_dir.mkdir(parents=True, exist_ok=True)
    summaries = {name: dist.summary(top_n) for name, dist in distributions.items()}
    for name, distribution in distributions.items():
        with gzip.open(histogram_dir / f"{name}.csv.gz", "wt", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("value", "count"))
            writer.writerows(sorted(distribution.frequencies.items()))
    result = {**metadata, "distributions": summaries}
    (output_dir / "results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def weighted_delta_entropy(result: dict[str, Any]) -> float:
    summaries = result["distributions"]
    names = ("interior", "planes", "edges", "vertex")
    total = sum(summaries[n]["count"] for n in names)
    return sum(summaries[n]["count"] * summaries[n]["entropy_bits_per_value"] for n in names) / total


def load_histogram(path: Path) -> tuple[np.ndarray, np.ndarray]:
    values: list[int] = []
    counts: list[int] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            values.append(int(row["value"]))
            counts.append(int(row["count"]))
    return np.asarray(values, dtype=np.int64), np.asarray(counts, dtype=np.int64)
