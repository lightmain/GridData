"""Benchmark storing hourly ERA5 TIFF files in HDF5 datasets.

The input is the directory of 744 tiled LZW TIFF files produced from the GRIB
file. Each benchmark writes a separate HDF5 file containing a 3D dataset with
dimensions ``time, latitude, longitude``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import h5py
import hdf5plugin
import numpy as np
import tifffile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_hdf5_benchmark"
DEFAULT_REPORT = PROJECT_ROOT / "HDF5Report.md"
DEFAULT_CHUNKS = (24, 128, 256)
DATASET_NAME = "temperature_2m"


@dataclass(frozen=True)
class CodecSpec:
    name: str
    dataset_kwargs: dict[str, Any]
    description: str


@dataclass(frozen=True)
class BenchmarkResult:
    codec: str
    status: str
    path: Path
    size_bytes: int | None
    elapsed_seconds: float
    throughput_mib_s: float | None
    dataset_kwargs: dict[str, Any]
    description: str
    readback_ok: bool
    error: str | None = None


def codec_specs() -> dict[str, CodecSpec]:
    return {
        "none": CodecSpec("none", {}, "No compression baseline"),
        "gzip_4_shuffle": CodecSpec(
            "gzip_4_shuffle",
            {"compression": "gzip", "compression_opts": 4, "shuffle": True},
            "Built-in HDF5 gzip level 4 with shuffle",
        ),
        "lzf_shuffle": CodecSpec(
            "lzf_shuffle",
            {"compression": "lzf", "shuffle": True},
            "h5py LZF with shuffle",
        ),
        "lz4": CodecSpec(
            "lz4",
            dict(hdf5plugin.LZ4()),
            "hdf5plugin LZ4",
        ),
        "blosc_lz4_bitshuffle": CodecSpec(
            "blosc_lz4_bitshuffle",
            dict(
                hdf5plugin.Blosc(
                    cname="lz4",
                    clevel=5,
                    shuffle=hdf5plugin.Blosc.BITSHUFFLE,
                )
            ),
            "hdf5plugin Blosc LZ4 level 5 with bitshuffle",
        ),
        "blosc_zstd_bitshuffle": CodecSpec(
            "blosc_zstd_bitshuffle",
            dict(
                hdf5plugin.Blosc(
                    cname="zstd",
                    clevel=5,
                    shuffle=hdf5plugin.Blosc.BITSHUFFLE,
                )
            ),
            "hdf5plugin Blosc Zstandard level 5 with bitshuffle",
        ),
        "zstd_1": CodecSpec(
            "zstd_1",
            dict(hdf5plugin.Zstd(clevel=1)),
            "hdf5plugin Zstandard level 1",
        ),
        "zstd_2": CodecSpec(
            "zstd_2",
            dict(hdf5plugin.Zstd(clevel=2)),
            "hdf5plugin Zstandard level 2",
        ),
        "zstd_3": CodecSpec(
            "zstd_3",
            dict(hdf5plugin.Zstd(clevel=3)),
            "hdf5plugin Zstandard level 3",
        ),
        "zstd_5": CodecSpec(
            "zstd_5",
            dict(hdf5plugin.Zstd(clevel=5)),
            "hdf5plugin Zstandard level 5",
        ),
        "zstd_9": CodecSpec(
            "zstd_9",
            dict(hdf5plugin.Zstd(clevel=9)),
            "hdf5plugin Zstandard level 9",
        ),
        "zstd_15": CodecSpec(
            "zstd_15",
            dict(hdf5plugin.Zstd(clevel=15)),
            "hdf5plugin Zstandard level 15",
        ),
        "zstd_19": CodecSpec(
            "zstd_19",
            dict(hdf5plugin.Zstd(clevel=19)),
            "hdf5plugin Zstandard level 19",
        ),
        "zstd_22": CodecSpec(
            "zstd_22",
            dict(hdf5plugin.Zstd(clevel=22)),
            "hdf5plugin Zstandard level 22",
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert ERA5 TIFF files to HDF5 and benchmark compressors."
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
        help=f"Directory for benchmark .h5 outputs. Default: {DEFAULT_OUTPUT_ROOT}",
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
        help="HDF5 chunks as time,latitude,longitude. Default: 24,128,256",
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
        help="Replace existing output .h5 files.",
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


def jsonable(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    return value


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


def coordinate_arrays(shape: tuple[int, int], time_count: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lat_count, lon_count = shape
    time = np.arange(time_count, dtype=np.int32)
    latitude = np.linspace(90.0, -90.0, lat_count, dtype=np.float32)
    longitude = np.linspace(0.0, 359.75, lon_count, dtype=np.float32)
    return time, latitude, longitude


def create_hdf5(
    files: list[Path],
    input_dir: Path,
    output_path: Path,
    chunks: tuple[int, int, int],
    spec: CodecSpec,
    overwrite: bool,
) -> tuple[float, int, bool]:
    if output_path.exists():
        if not overwrite:
            raise FileExistsError(f"{output_path} exists; use --overwrite to replace it")
        output_path.unlink()

    first = tifffile.imread(files[0])
    if first.ndim != 2:
        raise ValueError(f"Expected 2D TIFF input, got shape {first.shape}")

    shape = (len(files), *first.shape)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    time_values, latitudes, longitudes = coordinate_arrays(first.shape, len(files))

    started = time.perf_counter()
    with h5py.File(output_path, "w") as handle:
        handle.attrs.update(
            {
                "source_tiff_directory": str(input_dir),
                "variable": "2 metre temperature",
                "short_name": "2t",
                "units": "K",
                "grid_type": "regular_ll",
                "latitude_first": 90.0,
                "latitude_last": -90.0,
                "longitude_first": 0.0,
                "longitude_last": 359.75,
                "resolution_degrees": 0.25,
                "codec": spec.name,
                "codec_description": spec.description,
            }
        )
        handle.create_dataset("time", data=time_values)
        handle["time"].attrs["units"] = "hours since 2026-05-01 00:00:00"
        handle.create_dataset("latitude", data=latitudes)
        handle["latitude"].attrs["units"] = "degrees_north"
        handle.create_dataset("longitude", data=longitudes)
        handle["longitude"].attrs["units"] = "degrees_east"

        dataset = handle.create_dataset(
            DATASET_NAME,
            shape=shape,
            dtype="float32",
            chunks=chunks,
            fillvalue=np.nan,
            **spec.dataset_kwargs,
        )
        dataset.attrs.update(
            {
                "dimensions": json.dumps(["time", "latitude", "longitude"]),
                "shape": json.dumps(list(shape)),
                "chunks": json.dumps(list(chunks)),
                "dtype": "float32",
                "units": "K",
                "long_name": "2 metre temperature",
                "input_file_count": len(files),
            }
        )

        time_chunk = chunks[0]
        for start in range(0, len(files), time_chunk):
            stop = min(start + time_chunk, len(files))
            dataset[start:stop, :, :] = read_stack(files[start:stop])
            print(f"  wrote time slice {start}:{stop}", flush=True)

        handle.flush()
    elapsed = time.perf_counter() - started

    with h5py.File(output_path, "r") as handle:
        dataset = handle[DATASET_NAME]
        first_readback = dataset[0]
        last_readback = dataset[-1]
    first_ok = np.array_equal(first_readback, tifffile.imread(files[0]).astype(np.float32, copy=False))
    last_ok = np.array_equal(last_readback, tifffile.imread(files[-1]).astype(np.float32, copy=False))

    return elapsed, int(np.prod(shape) * np.dtype("float32").itemsize), bool(first_ok and last_ok)


def run_benchmark(
    files: list[Path],
    input_dir: Path,
    output_root: Path,
    chunks: tuple[int, int, int],
    codecs: list[CodecSpec],
    overwrite: bool,
) -> list[BenchmarkResult]:
    output_root.mkdir(parents=True, exist_ok=True)
    results: list[BenchmarkResult] = []
    for spec in codecs:
        output_path = output_root / f"{spec.name}.h5"
        print(f"Running codec: {spec.name} -> {output_path}", flush=True)
        started = time.perf_counter()
        try:
            elapsed, uncompressed_bytes, readback_ok = create_hdf5(
                files=files,
                input_dir=input_dir,
                output_path=output_path,
                chunks=chunks,
                spec=spec,
                overwrite=overwrite,
            )
            size_bytes = output_path.stat().st_size
            throughput = (uncompressed_bytes / (1024 * 1024)) / elapsed
            results.append(
                BenchmarkResult(
                    codec=spec.name,
                    status="ok",
                    path=output_path,
                    size_bytes=size_bytes,
                    elapsed_seconds=elapsed,
                    throughput_mib_s=throughput,
                    dataset_kwargs=jsonable(spec.dataset_kwargs),
                    description=spec.description,
                    readback_ok=readback_ok,
                )
            )
            print(
                f"  done: {format_bytes(size_bytes)} in {elapsed:.2f}s "
                f"({throughput:.2f} MiB/s), readback_ok={readback_ok}",
                flush=True,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started
            results.append(
                BenchmarkResult(
                    codec=spec.name,
                    status="failed",
                    path=output_path,
                    size_bytes=output_path.stat().st_size if output_path.exists() else None,
                    elapsed_seconds=elapsed,
                    throughput_mib_s=None,
                    dataset_kwargs=jsonable(spec.dataset_kwargs),
                    description=spec.description,
                    readback_ok=False,
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
        "# HDF5 Compression Report",
        "",
        "## Input",
        "",
        f"- TIFF directory: `{input_dir}`",
        f"- TIFF file count: `{len(files)}`",
        f"- Dataset: `/{DATASET_NAME}`",
        f"- Array shape: `{shape}` (`time, latitude, longitude`)",
        "- Data type: `float32`",
        f"- Uncompressed array bytes: `{uncompressed_bytes}` ({format_bytes(uncompressed_bytes)})",
        f"- HDF5 chunks: `{chunks}`",
        f"- Output root: `{output_root}`",
        "",
        "## Compression Notes",
        "",
        "- HDF5 compression is configured per dataset through the filter pipeline.",
        "- Compressed HDF5 datasets must use chunked layout; this benchmark uses `(24, 128, 256)` unless overridden.",
        "- This report only benchmarks lossless methods: built-in gzip/lzf and hdf5plugin LZ4/Blosc/Zstd filters.",
        "- `shuffle` and `bitshuffle` are lossless prefilters intended to improve compression of numeric arrays.",
        "- Built-in gzip level 9 was probed separately and skipped from the full benchmark because it completed only one 24-hour chunk in about 30 seconds.",
        "",
        "## Environment",
        "",
        f"- h5py: `{h5py.__version__}`",
        f"- HDF5: `{h5py.version.hdf5_version}`",
        f"- hdf5plugin: `{hdf5plugin.version}`",
        f"- numpy: `{np.__version__}`",
        f"- tifffile: `{tifffile.__version__}`",
        "",
        "## Results",
        "",
        "| Codec | Status | Readback | Size | Ratio vs Raw | Write Time (s) | Throughput (MiB/s) | Path |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
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
            f"`{result.codec}` | {result.status} | {result.readback_ok} | "
            f"{format_bytes(result.size_bytes)} | {ratio} | "
            f"{result.elapsed_seconds:.2f} | {throughput} | `{result.path}` |"
        )

    lines.extend(["", "## Codec Configs", ""])
    for result in results:
        lines.append(f"### `{result.codec}`")
        lines.append("")
        lines.append(result.description)
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(result.dataset_kwargs, indent=2, sort_keys=True))
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
        input_dir = args.input_dir.resolve()
        files = tiff_files(input_dir, args.limit)
        results = run_benchmark(
            files=files,
            input_dir=input_dir,
            output_root=args.output_root.resolve(),
            chunks=chunks,
            codecs=codecs,
            overwrite=args.overwrite,
        )
        write_report(
            report_path=args.report.resolve(),
            input_dir=input_dir,
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
