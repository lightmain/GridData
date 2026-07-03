"""Benchmark storing hourly ERA5 TIFF files in Zarr arrays.

The input is the directory of 744 tiled LZW TIFF files produced from the GRIB
file. Each benchmark writes a separate 3D Zarr array with dimensions
``time, latitude, longitude``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import tifffile
import zarr
from numcodecs import Blosc, GZip, Zlib, Zstd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_zarr_benchmark"
DEFAULT_REPORT = PROJECT_ROOT / "ZarrReport.md"
DEFAULT_CHUNKS = (24, 128, 256)


@dataclass(frozen=True)
class CodecSpec:
    name: str
    compressor: Any
    description: str


@dataclass(frozen=True)
class BenchmarkResult:
    codec: str
    status: str
    path: Path
    size_bytes: int | None
    elapsed_seconds: float
    throughput_mib_s: float | None
    compressor_config: dict[str, Any] | None
    error: str | None = None


def codec_specs() -> dict[str, CodecSpec]:
    return {
        "none": CodecSpec("none", None, "No compressor baseline"),
        "blosc_lz4_bitshuffle": CodecSpec(
            "blosc_lz4_bitshuffle",
            Blosc(cname="lz4", clevel=5, shuffle=Blosc.BITSHUFFLE),
            "Blosc LZ4 level 5 with bitshuffle",
        ),
        "blosc_zstd_bitshuffle": CodecSpec(
            "blosc_zstd_bitshuffle",
            Blosc(cname="zstd", clevel=5, shuffle=Blosc.BITSHUFFLE),
            "Blosc Zstandard level 5 with bitshuffle",
        ),
        "blosc_zstd_shuffle": CodecSpec(
            "blosc_zstd_shuffle",
            Blosc(cname="zstd", clevel=5, shuffle=Blosc.SHUFFLE),
            "Blosc Zstandard level 5 with byte shuffle",
        ),
        "zstd_5": CodecSpec("zstd_5", Zstd(level=5), "Zstandard level 5"),
        "zlib_6": CodecSpec("zlib_6", Zlib(level=6), "Zlib level 6"),
        "gzip_6": CodecSpec("gzip_6", GZip(level=6), "GZip level 6"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert ERA5 TIFF files to Zarr and benchmark compressors."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing TIFF files. Default: {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help=f"Directory for benchmark .zarr outputs. Default: {DEFAULT_OUTPUT_ROOT}",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"Markdown report path. Default: {DEFAULT_REPORT}",
    )
    parser.add_argument(
        "--codecs",
        nargs="+",
        default=["blosc_zstd_bitshuffle"],
        help="Codec names to run, or 'all'.",
    )
    parser.add_argument(
        "--chunks",
        default=",".join(str(value) for value in DEFAULT_CHUNKS),
        help="Zarr chunks as time,latitude,longitude. Default: 24,128,256",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only convert the first N TIFF files.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing output .zarr directories.",
    )
    return parser.parse_args()


def parse_chunks(value: str) -> tuple[int, int, int]:
    parts = tuple(int(part.strip()) for part in value.split(","))
    if len(parts) != 3 or any(part < 1 for part in parts):
        raise ValueError("chunks must be three positive integers: time,latitude,longitude")
    return parts


def resolve_codecs(names: Iterable[str]) -> list[CodecSpec]:
    specs = codec_specs()
    requested = list(names)
    if requested == ["all"]:
        return list(specs.values())

    unknown = sorted(set(requested) - set(specs))
    if unknown:
        raise ValueError(f"Unknown codec(s): {', '.join(unknown)}")
    return [specs[name] for name in requested]


def tiff_files(input_dir: Path, limit: int | None) -> list[Path]:
    files = sorted(input_dir.glob("*.tif*"))
    if limit is not None:
        files = files[:limit]
    if not files:
        raise FileNotFoundError(f"No TIFF files found in {input_dir}")
    return files


def directory_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def format_bytes(size: int | None) -> str:
    if size is None:
        return "n/a"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def compressor_config(compressor: Any) -> dict[str, Any] | None:
    if compressor is None:
        return None
    config = compressor.get_config()
    return {key: value for key, value in config.items()}


def read_stack(files: list[Path]) -> np.ndarray:
    first = tifffile.imread(files[0])
    stack = np.empty((len(files), *first.shape), dtype=np.float32)
    stack[0] = first.astype(np.float32, copy=False)
    for index, path in enumerate(files[1:], start=1):
        image = tifffile.imread(path)
        if image.shape != first.shape:
            raise ValueError(f"{path} has shape {image.shape}, expected {first.shape}")
        stack[index] = image.astype(np.float32, copy=False)
    return stack


def create_zarr(
    files: list[Path],
    input_dir: Path,
    output_path: Path,
    chunks: tuple[int, int, int],
    compressor: Any,
    overwrite: bool,
) -> tuple[float, int]:
    if output_path.exists():
        if not overwrite:
            raise FileExistsError(f"{output_path} exists; use --overwrite to replace it")
        shutil.rmtree(output_path)

    first = tifffile.imread(files[0])
    if first.ndim != 2:
        raise ValueError(f"Expected 2D TIFF input, got shape {first.shape}")

    shape = (len(files), *first.shape)
    array = zarr.open(
        str(output_path),
        mode="w",
        shape=shape,
        chunks=chunks,
        dtype="float32",
        compressor=compressor,
        fill_value=np.nan,
    )
    array.attrs.update(
        {
            "source_tiff_directory": str(input_dir),
            "dimensions": ["time", "latitude", "longitude"],
            "shape": list(shape),
            "chunks": list(chunks),
            "dtype": "float32",
            "variable": "2 metre temperature",
            "short_name": "2t",
            "units": "K",
            "grid": {
                "latitude_points": int(first.shape[0]),
                "longitude_points": int(first.shape[1]),
                "latitude_first": 90.0,
                "latitude_last": -90.0,
                "longitude_first": 0.0,
                "longitude_last": 359.75,
                "resolution_degrees": 0.25,
            },
            "input_files": [path.name for path in files],
        }
    )

    started = time.perf_counter()
    time_chunk = chunks[0]
    for start in range(0, len(files), time_chunk):
        stop = min(start + time_chunk, len(files))
        array[start:stop, :, :] = read_stack(files[start:stop])
        print(f"  wrote time slice {start}:{stop}", flush=True)
    elapsed = time.perf_counter() - started

    # Force a small readback so failures surface during the benchmark.
    sample = array[0, 0:4, 0:4]
    if sample.shape != (4, 4):
        raise ValueError(f"Unexpected readback sample shape: {sample.shape}")

    return elapsed, int(np.prod(shape) * np.dtype("float32").itemsize)


def run_benchmark(
    files: list[Path],
    output_root: Path,
    chunks: tuple[int, int, int],
    codecs: list[CodecSpec],
    overwrite: bool,
) -> list[BenchmarkResult]:
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[BenchmarkResult] = []
    for spec in codecs:
        output_path = output_root / f"{spec.name}.zarr"
        print(f"Running codec: {spec.name} -> {output_path}", flush=True)
        started = time.perf_counter()
        try:
            elapsed, uncompressed_bytes = create_zarr(
                files=files,
                input_dir=files[0].parent,
                output_path=output_path,
                chunks=chunks,
                compressor=spec.compressor,
                overwrite=overwrite,
            )
            size_bytes = directory_size(output_path)
            throughput = (uncompressed_bytes / (1024 * 1024)) / elapsed
            results.append(
                BenchmarkResult(
                    codec=spec.name,
                    status="ok",
                    path=output_path,
                    size_bytes=size_bytes,
                    elapsed_seconds=elapsed,
                    throughput_mib_s=throughput,
                    compressor_config=compressor_config(spec.compressor),
                )
            )
            print(
                f"  done: {format_bytes(size_bytes)} in {elapsed:.2f}s "
                f"({throughput:.2f} MiB/s)",
                flush=True,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started
            results.append(
                BenchmarkResult(
                    codec=spec.name,
                    status="failed",
                    path=output_path,
                    size_bytes=directory_size(output_path) if output_path.exists() else None,
                    elapsed_seconds=elapsed,
                    throughput_mib_s=None,
                    compressor_config=compressor_config(spec.compressor),
                    error=str(exc),
                )
            )
            print(f"  failed after {elapsed:.2f}s: {exc}", flush=True)
    return results


def write_report(
    report_path: Path,
    input_dir: Path,
    output_root: Path,
    files: list[Path],
    chunks: tuple[int, int, int],
    results: list[BenchmarkResult],
) -> None:
    first = tifffile.imread(files[0])
    shape = (len(files), *first.shape)
    uncompressed_bytes = int(np.prod(shape) * np.dtype("float32").itemsize)
    sorted_results = sorted(
        results,
        key=lambda result: result.size_bytes if result.size_bytes is not None else sys.maxsize,
    )

    lines = [
        "# Zarr Compression Report",
        "",
        "## Input",
        "",
        f"- TIFF directory: `{input_dir}`",
        f"- TIFF file count: `{len(files)}`",
        f"- Array shape: `{shape}` (`time, latitude, longitude`)",
        "- Data type: `float32`",
        f"- Uncompressed array bytes: `{uncompressed_bytes}` ({format_bytes(uncompressed_bytes)})",
        f"- Zarr chunks: `{chunks}`",
        f"- Output root: `{output_root}`",
        "",
        "## Results",
        "",
        "| Codec | Status | Size | Ratio vs Raw | Write Time (s) | Throughput (MiB/s) | Path |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for result in sorted_results:
        ratio = (
            f"{uncompressed_bytes / result.size_bytes:.2f}x"
            if result.size_bytes
            else "n/a"
        )
        throughput = (
            f"{result.throughput_mib_s:.2f}"
            if result.throughput_mib_s is not None
            else "n/a"
        )
        lines.append(
            "| "
            f"`{result.codec}` | {result.status} | {format_bytes(result.size_bytes)} | "
            f"{ratio} | {result.elapsed_seconds:.2f} | {throughput} | "
            f"`{result.path}` |"
        )

    lines.extend(
        [
            "",
            "## Codec Selection Notes",
            "",
            "- The benchmark uses Zarr v2 because Zarr 3.1.6 stalled during local array creation in this environment.",
            "- The tested codecs come from numcodecs' documented compression codecs: Blosc, GZip, Zlib, and Zstd.",
            "- Blosc was tested with LZ4 and Zstd backends plus shuffle/bitshuffle variants because these are common choices for numeric arrays.",
            "",
            "",
            "## Compressor Configs",
            "",
        ]
    )
    for result in results:
        lines.append(f"### `{result.codec}`")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(result.compressor_config, indent=2, sort_keys=True))
        lines.append("```")
        if result.error:
            lines.append("")
            lines.append(f"Error: `{result.error}`")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    try:
        chunks = parse_chunks(args.chunks)
        codecs = resolve_codecs(args.codecs)
        files = tiff_files(args.input_dir.resolve(), args.limit)
        results = run_benchmark(
            files=files,
            output_root=args.output_root.resolve(),
            chunks=chunks,
            codecs=codecs,
            overwrite=args.overwrite,
        )
        write_report(
            report_path=args.report.resolve(),
            input_dir=args.input_dir.resolve(),
            output_root=args.output_root.resolve(),
            files=files,
            chunks=chunks,
            results=results,
        )
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
