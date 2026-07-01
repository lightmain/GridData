"""Store TIFF time series as block-wise naive temporal differences.

For each block, the first TIFF is copied unchanged. Every following TIFF in the
same block stores ``current_original - previous_original`` as float32 data.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import tifffile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026-naive_diff"
DEFAULT_BLOCK_SIZE = 100
DEFAULT_TILE_SIZE = 256
DEFAULT_COMPRESSION = "lzma"
DEFAULT_COMPRESSION_LEVEL = 9


@dataclass
class RunningStats:
    count: int = 0
    min_value: float | None = None
    max_value: float | None = None
    mean: float = 0.0
    m2: float = 0.0

    def update(self, values: np.ndarray) -> None:
        flat = np.asarray(values, dtype=np.float64).ravel()
        if flat.size == 0:
            return

        batch_count = int(flat.size)
        batch_min = float(np.min(flat))
        batch_max = float(np.max(flat))
        batch_mean = float(np.mean(flat))
        centered = flat - batch_mean
        batch_m2 = float(np.dot(centered, centered))

        if self.count == 0:
            self.count = batch_count
            self.min_value = batch_min
            self.max_value = batch_max
            self.mean = batch_mean
            self.m2 = batch_m2
            return

        previous_count = self.count
        new_count = previous_count + batch_count
        delta = batch_mean - self.mean

        self.min_value = min(self.min_value, batch_min) if self.min_value is not None else batch_min
        self.max_value = max(self.max_value, batch_max) if self.max_value is not None else batch_max
        self.mean += delta * batch_count / new_count
        self.m2 += batch_m2 + delta * delta * previous_count * batch_count / new_count
        self.count = new_count

    @property
    def variance(self) -> float:
        return self.m2 / self.count if self.count else float("nan")

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "min": self.min_value,
            "max": self.max_value,
            "mean": self.mean,
            "variance": self.variance,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a TIFF time series to block-wise naive differences."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing input TIFF files. Default: {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for naive-diff TIFF files. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--block-size",
        type=int,
        default=DEFAULT_BLOCK_SIZE,
        help="Number of time steps per independently decodable block. Default: 100",
    )
    parser.add_argument(
        "--tile-size",
        type=int,
        default=DEFAULT_TILE_SIZE,
        help="Square tile size for diff TIFF files. Default: 256",
    )
    parser.add_argument(
        "--compression",
        default=DEFAULT_COMPRESSION,
        help=f"TIFF compression for diff files. Default: {DEFAULT_COMPRESSION}",
    )
    parser.add_argument(
        "--compression-level",
        type=int,
        default=DEFAULT_COMPRESSION_LEVEL,
        help=f"Compression level for codecs that support it. Default: {DEFAULT_COMPRESSION_LEVEL}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of input TIFF files to process.",
    )
    return parser.parse_args()


def tiff_files(input_dir: Path) -> list[Path]:
    files = sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}
    )
    if not files:
        raise FileNotFoundError(f"No TIFF files found in {input_dir}")
    return files


def compression_args(compression: str, level: int | None) -> dict[str, Any] | None:
    if level is None:
        return None
    if compression.lower() in {"zstd", "deflate", "adobe_deflate"}:
        return {"level": level}
    return None


def write_diff_tiff(
    target: Path,
    diff: np.ndarray,
    source: Path,
    previous_source: Path,
    block_index: int,
    block_offset: int,
    tile_size: int,
    compression: str,
    level: int | None,
    overwrite: bool,
) -> None:
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} already exists; use --overwrite to replace it")

    description = {
        "method": "naive_diff",
        "value": "current_original_minus_previous_original",
        "source_file": source.name,
        "previous_source_file": previous_source.name,
        "block_index": block_index,
        "block_offset": block_offset,
        "dtype": "float32",
    }
    kwargs: dict[str, Any] = {
        "photometric": "minisblack",
        "compression": compression,
        "predictor": True,
        "tile": (tile_size, tile_size),
        "description": json.dumps(description, sort_keys=True, separators=(",", ":")),
        "metadata": None,
    }
    codec_args = compression_args(compression, level)
    if codec_args is not None:
        kwargs["compressionargs"] = codec_args

    tifffile.imwrite(target, diff.astype(np.float32, copy=False), **kwargs)


def copy_key_frame(source: Path, target: Path, overwrite: bool) -> None:
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} already exists; use --overwrite to replace it")
    shutil.copy2(source, target)


def convert(
    input_dir: Path,
    output_dir: Path,
    block_size: int,
    tile_size: int,
    compression: str,
    compression_level: int | None,
    overwrite: bool,
    limit: int | None,
) -> dict[str, Any]:
    if block_size < 2:
        raise ValueError("--block-size must be >= 2")
    if tile_size < 16:
        raise ValueError("--tile-size must be >= 16")
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    files = tiff_files(input_dir)
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be >= 1 when provided")
        files = files[:limit]
    output_dir.mkdir(parents=True, exist_ok=True)

    original_stats = RunningStats()
    diff_stats = RunningStats()
    key_frame_count = 0
    diff_frame_count = 0
    previous_original: np.ndarray | None = None
    previous_path: Path | None = None

    for index, source in enumerate(files):
        block_index = index // block_size
        block_offset = index % block_size
        target = output_dir / source.name

        current = tifffile.imread(source).astype(np.float32, copy=False)
        original_stats.update(current)

        if block_offset == 0:
            copy_key_frame(source, target, overwrite=overwrite)
            key_frame_count += 1
        else:
            if previous_original is None or previous_path is None:
                raise RuntimeError("Missing previous image for diff frame")
            diff = current - previous_original
            diff_stats.update(diff)
            write_diff_tiff(
                target=target,
                diff=diff,
                source=source,
                previous_source=previous_path,
                block_index=block_index,
                block_offset=block_offset,
                tile_size=tile_size,
                compression=compression,
                level=compression_level,
                overwrite=overwrite,
            )
            diff_frame_count += 1

        previous_original = current
        previous_path = source

        print(
            f"[{index + 1}/{len(files)}] "
            f"{'key' if block_offset == 0 else 'diff'} -> {target}",
            flush=True,
        )

    return {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "file_count": len(files),
        "block_size": block_size,
        "key_frame_count": key_frame_count,
        "diff_frame_count": diff_frame_count,
        "diff_compression": compression,
        "diff_compression_level": compression_level,
        "original_stats": original_stats.as_dict(),
        "diff_stats_excluding_key_frames": diff_stats.as_dict(),
    }


def main() -> int:
    args = parse_args()
    try:
        summary = convert(
            input_dir=args.input_dir.resolve(),
            output_dir=args.output_dir.resolve(),
            block_size=args.block_size,
            tile_size=args.tile_size,
            compression=args.compression,
            compression_level=args.compression_level,
            overwrite=args.overwrite,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1

    print()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
