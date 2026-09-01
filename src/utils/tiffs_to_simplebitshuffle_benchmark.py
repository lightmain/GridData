"""Benchmark a simple bit-plane transform followed by independent Zstd compression.

The input TIFF files are read in filename order and interpreted as one C-order
``float32`` tensor.  Each IEEE-754 value is split into 32 bit planes.  Values in
each plane keep the same logical order as values in the original tensor, and
the 0/1 sequence is packed to one bit per value before it is fed to Zstandard.

Each bit plane is stored as one continuous Zstd frame.  This is intentionally
an early, format-free experiment: there are no Zarr/HDF5 chunks, coordinates,
TIFF metadata, or container metadata in the compressed output.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np
import tifffile
import zstandard as zstd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "data" / "ERA5-temperature-May2026_simplebitshuffle_benchmark"
)
DEFAULT_REPORT = PROJECT_ROOT / "SimpleBitshuffleReport.md"
DEFAULT_ZSTD_LEVEL = 3
BIT_COUNT = 32


@dataclass(frozen=True)
class BitPlaneResult:
    bit_index: int
    ieee754_field: str
    path: Path
    value_count: int
    uncompressed_bytes: int
    compressed_bytes: int

    @property
    def compression_ratio(self) -> float:
        return self.uncompressed_bytes / self.compressed_bytes


@dataclass(frozen=True)
class BenchmarkSummary:
    input_dir: Path
    output_dir: Path
    file_count: int
    image_shape: tuple[int, int]
    tensor_shape: tuple[int, int, int]
    value_count: int
    raw_float32_bytes: int
    bit_plane_bytes: int
    total_bit_plane_bytes: int
    total_compressed_bytes: int
    zstd_level: int
    elapsed_seconds: float
    throughput_mib_s: float
    results: tuple[BitPlaneResult, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Split a float32 TIFF tensor into 32 packed bit planes and "
            "benchmark independent Zstd compression."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing TIFF files. Default: {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for the 32 .zst bit-plane files. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"Markdown report path. Default: {DEFAULT_REPORT}",
    )
    parser.add_argument(
        "--zstd-level",
        type=int,
        default=DEFAULT_ZSTD_LEVEL,
        help=f"Zstandard compression level. Default: {DEFAULT_ZSTD_LEVEL}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N TIFF files.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing bit-plane .zst files and report.",
    )
    return parser.parse_args()


def tiff_files(input_dir: Path, limit: int | None) -> list[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be >= 1 when provided")

    files = sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".tif", ".tiff"}
    )
    if limit is not None:
        files = files[:limit]
    if not files:
        raise FileNotFoundError(f"No TIFF files found in {input_dir}")
    return files


def ieee754_field(bit_index: int) -> str:
    if 0 <= bit_index <= 22:
        return "mantissa"
    if 23 <= bit_index <= 30:
        return "exponent"
    if bit_index == 31:
        return "sign"
    raise ValueError(f"Invalid float32 bit index: {bit_index}")


def bit_plane_filename(bit_index: int) -> str:
    suffix = "lsb" if bit_index == 0 else "sign" if bit_index == 31 else ieee754_field(bit_index)
    return f"bit_{bit_index:02d}_{suffix}.zst"


def read_float32_image(path: Path, expected_shape: tuple[int, int] | None) -> np.ndarray:
    image = tifffile.imread(path)
    if image.ndim != 2:
        raise ValueError(f"Expected a 2D TIFF, got {path} with shape {image.shape}")
    if expected_shape is not None and image.shape != expected_shape:
        raise ValueError(f"{path} has shape {image.shape}, expected {expected_shape}")

    # Canonical little-endian float32 makes bit numbering deterministic.  The
    # resulting C-contiguous array is flattened in the original tensor order.
    return np.asarray(image, dtype="<f4", order="C")


def write_compressed(
    handle: BinaryIO,
    compressor: zstd.ZstdCompressionObj,
    packed: np.ndarray,
) -> None:
    if packed.size:
        compressed = compressor.compress(packed.tobytes())
        if compressed:
            handle.write(compressed)


def prepare_output_paths(output_dir: Path, overwrite: bool) -> tuple[list[Path], list[Path]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    targets = [output_dir / bit_plane_filename(bit) for bit in range(BIT_COUNT)]
    temporary = [target.with_name(target.name + ".tmp") for target in targets]

    existing = [target for target in targets if target.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            f"{existing[0]} already exists; use --overwrite to replace benchmark outputs"
        )

    # These are narrowly scoped leftovers from an interrupted run.
    for path in temporary:
        if path.exists():
            path.unlink()
    return targets, temporary


def run_benchmark(
    files: list[Path],
    input_dir: Path,
    output_dir: Path,
    zstd_level: int,
    overwrite: bool,
) -> BenchmarkSummary:
    first = read_float32_image(files[0], expected_shape=None)
    image_shape = tuple(int(value) for value in first.shape)
    values_per_image = int(first.size)
    value_count = values_per_image * len(files)
    bit_plane_bytes = math.ceil(value_count / 8)
    raw_float32_bytes = value_count * np.dtype("float32").itemsize

    targets, temporary = prepare_output_paths(output_dir, overwrite)
    handles: list[BinaryIO] = []
    compressors: list[zstd.ZstdCompressionObj] = []
    # At most seven logical bits per plane are carried between TIFFs.  This
    # prevents padding at file boundaries, so every output is one global plane.
    remainders = [np.empty(0, dtype=np.uint8) for _ in range(BIT_COUNT)]

    started = time.perf_counter()
    try:
        for path in temporary:
            handles.append(path.open("wb"))
            compressors.append(
                zstd.ZstdCompressor(
                    level=zstd_level,
                    write_checksum=True,
                    write_content_size=False,
                ).compressobj()
            )

        for file_index, path in enumerate(files):
            image = first if file_index == 0 else read_float32_image(path, image_shape)
            words = image.view("<u4").reshape(-1)

            for bit_index in range(BIT_COUNT):
                bits = ((words >> bit_index) & 1).astype(np.uint8)
                remainder = remainders[bit_index]
                if remainder.size:
                    bits = np.concatenate((remainder, bits))

                packed_value_count = (bits.size // 8) * 8
                packed = np.packbits(
                    bits[:packed_value_count],
                    bitorder="little",
                )
                write_compressed(handles[bit_index], compressors[bit_index], packed)
                remainders[bit_index] = bits[packed_value_count:].copy()

            print(f"[{file_index + 1}/{len(files)}] processed {path.name}", flush=True)

        for bit_index in range(BIT_COUNT):
            remainder = remainders[bit_index]
            if remainder.size:
                # np.packbits pads only the final byte of the global bit plane.
                packed = np.packbits(remainder, bitorder="little")
                write_compressed(handles[bit_index], compressors[bit_index], packed)

            final_bytes = compressors[bit_index].flush()
            if final_bytes:
                handles[bit_index].write(final_bytes)
            handles[bit_index].flush()
            handles[bit_index].close()

        handles.clear()
        for source, target in zip(temporary, targets, strict=True):
            source.replace(target)
    except Exception:
        for handle in handles:
            try:
                handle.close()
            except Exception:
                pass
        for path in temporary:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass
        raise

    elapsed = time.perf_counter() - started
    results = tuple(
        BitPlaneResult(
            bit_index=bit_index,
            ieee754_field=ieee754_field(bit_index),
            path=targets[bit_index],
            value_count=value_count,
            uncompressed_bytes=bit_plane_bytes,
            compressed_bytes=targets[bit_index].stat().st_size,
        )
        for bit_index in range(BIT_COUNT)
    )
    total_bit_plane_bytes = bit_plane_bytes * BIT_COUNT
    total_compressed_bytes = sum(result.compressed_bytes for result in results)
    throughput_mib_s = (raw_float32_bytes / (1024 * 1024)) / elapsed

    return BenchmarkSummary(
        input_dir=input_dir,
        output_dir=output_dir,
        file_count=len(files),
        image_shape=image_shape,
        tensor_shape=(len(files), *image_shape),
        value_count=value_count,
        raw_float32_bytes=raw_float32_bytes,
        bit_plane_bytes=bit_plane_bytes,
        total_bit_plane_bytes=total_bit_plane_bytes,
        total_compressed_bytes=total_compressed_bytes,
        zstd_level=zstd_level,
        elapsed_seconds=elapsed,
        throughput_mib_s=throughput_mib_s,
        results=results,
    )


def format_bytes(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def write_report(report_path: Path, summary: BenchmarkSummary, overwrite: bool) -> None:
    if report_path.exists() and not overwrite:
        raise FileExistsError(f"{report_path} exists; use --overwrite to replace it")

    overall_ratio = summary.raw_float32_bytes / summary.total_compressed_bytes
    padding_bytes = summary.total_bit_plane_bytes - summary.raw_float32_bytes
    lines = [
        "# Simple Bitshuffle + Zstandard Report",
        "",
        "## Input",
        "",
        f"- TIFF directory: `{summary.input_dir}`",
        f"- TIFF file count: `{summary.file_count}`",
        f"- Tensor shape: `{summary.tensor_shape}` (`time, latitude, longitude`)",
        "- Tensor order: C-order; TIFF files sorted by filename",
        "- Source value type: `float32` (IEEE-754 binary32)",
        f"- Float value count: `{summary.value_count}`",
        f"- Raw float32 bytes: `{summary.raw_float32_bytes}` ({format_bytes(summary.raw_float32_bytes)})",
        "",
        "## Transform",
        "",
        "- Bit index 0 is the least-significant mantissa bit; bit 31 is the sign bit.",
        "- Each plane preserves the flattened C-order of the original float32 tensor.",
        "- Each 0/1 plane is packed with `numpy.packbits(bitorder='little')` before compression.",
        "- Each plane is compressed independently as one continuous Zstandard frame.",
        "- No array chunks, coordinates, TIFF metadata, or container metadata are stored.",
        f"- Zstandard level: `{summary.zstd_level}`",
        "",
        "## Per-bit Results",
        "",
        "| Bit | IEEE-754 field | Values | Before Zstd | After Zstd | Compression ratio | Output |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    for result in summary.results:
        lines.append(
            f"| {result.bit_index} | {result.ieee754_field} | "
            f"{result.value_count} | {result.uncompressed_bytes} B | "
            f"{result.compressed_bytes} B | {result.compression_ratio:.3f}x | "
            f"`{result.path}` |"
        )

    lines.extend(
        [
            "",
            "## Total",
            "",
            f"- 32 packed planes before Zstd: `{summary.total_bit_plane_bytes}` ({format_bytes(summary.total_bit_plane_bytes)})",
            f"- Final compressed bytes: `{summary.total_compressed_bytes}` ({format_bytes(summary.total_compressed_bytes)})",
            f"- Overall compression ratio vs raw float32: `{overall_ratio:.3f}x`",
            f"- Final size vs raw float32: `{summary.total_compressed_bytes / summary.raw_float32_bytes:.3%}`",
            f"- Final-byte padding across all planes: `{padding_bytes}` B",
            f"- End-to-end elapsed time: `{summary.elapsed_seconds:.3f}` s",
            f"- End-to-end throughput: `{summary.throughput_mib_s:.2f}` MiB/s",
            "",
            "The timing includes TIFF decoding, bit-plane extraction, bit packing, Zstandard compression, and disk writes.",
            "",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def check_report_target(report_path: Path, overwrite: bool) -> None:
    if report_path.exists() and not overwrite:
        raise FileExistsError(f"{report_path} exists; use --overwrite to replace it")


def print_summary(summary: BenchmarkSummary, report_path: Path) -> None:
    payload = {
        "input_dir": str(summary.input_dir),
        "output_dir": str(summary.output_dir),
        "report": str(report_path),
        "file_count": summary.file_count,
        "tensor_shape": summary.tensor_shape,
        "value_count": summary.value_count,
        "raw_float32_bytes": summary.raw_float32_bytes,
        "bit_plane_bytes": summary.bit_plane_bytes,
        "total_bit_plane_bytes": summary.total_bit_plane_bytes,
        "total_compressed_bytes": summary.total_compressed_bytes,
        "compression_ratio": summary.raw_float32_bytes / summary.total_compressed_bytes,
        "elapsed_seconds": summary.elapsed_seconds,
        "throughput_mib_s": summary.throughput_mib_s,
    }
    print()
    print(json.dumps(payload, indent=2))


def main() -> int:
    args = parse_args()
    try:
        input_dir = args.input_dir.resolve()
        output_dir = args.output_dir.resolve()
        report_path = args.report.resolve()
        check_report_target(report_path, overwrite=args.overwrite)
        files = tiff_files(input_dir, args.limit)
        summary = run_benchmark(
            files=files,
            input_dir=input_dir,
            output_dir=output_dir,
            zstd_level=args.zstd_level,
            overwrite=args.overwrite,
        )
        write_report(report_path, summary, overwrite=args.overwrite)
        print_summary(summary, report_path)
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
