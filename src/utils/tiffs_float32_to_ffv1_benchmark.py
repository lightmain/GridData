"""Store float32 TIFF bit patterns as four lossless FFV1 byte channels."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import tifffile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_ffv1_benchmark"
DEFAULT_REPORT = PROJECT_ROOT / "FFV1Report.md"
DEFAULT_GRIB = PROJECT_ROOT / "data" / "ERA5-temperature-May2026.grib"

# FFmpeg's BGRA byte order maps directly to the four bytes of each little-endian
# float32: B=bits 0..7, G=8..15, R=16..23, A=24..31.
PROFILES = {
    "ffv1_rice_context0": ["-level", "3", "-coder", "0", "-context", "0", "-g", "1", "-slicecrc", "1"],
    "ffv1_range_context1": ["-level", "3", "-coder", "1", "-context", "1", "-g", "1", "-slicecrc", "1"],
}


@dataclass(frozen=True)
class Result:
    profile: str
    output: str
    output_bytes: int
    raw_float32_ratio: float
    tiff_ratio: float
    grib_ratio: float | None
    encode_seconds: float
    encode_mib_s: float
    verify_seconds: float
    bitwise_identical: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--grib", type=Path, default=DEFAULT_GRIB)
    parser.add_argument("--profiles", nargs="+", choices=PROFILES, default=list(PROFILES))
    parser.add_argument("--fps", type=float, default=24.0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def input_files(path: Path, limit: int | None) -> list[Path]:
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")
    files = sorted(item for item in path.glob("*.tif*") if item.is_file())
    files = files[:limit] if limit is not None else files
    if not files:
        raise FileNotFoundError(f"No TIFF files found in {path}")
    return files


def read_float32_le(path: Path, shape: tuple[int, int]) -> np.ndarray:
    array = np.asarray(tifffile.imread(path), dtype="<f4", order="C")
    if array.shape != shape:
        raise ValueError(f"{path} has shape {array.shape}, expected {shape}")
    return array


def encode(
    files: list[Path], shape: tuple[int, int], fps: float, profile: str, output: Path,
) -> float:
    height, width = shape
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "bgra", "-video_size", f"{width}x{height}",
        "-framerate", str(fps), "-i", "pipe:0", "-an", "-c:v", "ffv1",
        *PROFILES[profile], "-pix_fmt", "bgra",
        "-metadata:s:v:0", "byte_layout=B:float_bits_0_7,G:8_15,R:16_23,A:24_31",
        "-f", "matroska", str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    started = time.perf_counter()
    try:
        for path in files:
            # The C-contiguous little-endian float array already has exactly
            # the byte sequence expected by a packed BGRA raw video frame.
            process.stdin.write(read_float32_le(path, shape).view(np.uint8).tobytes())
        process.stdin.close()
        return_code = process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    if return_code:
        raise RuntimeError(f"FFmpeg encoding failed with exit code {return_code}")
    return time.perf_counter() - started


def verify(output: Path, files: list[Path], shape: tuple[int, int]) -> tuple[float, bool]:
    height, width = shape
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
        "-f", "rawvideo", "-pix_fmt", "bgra", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    assert process.stdout is not None
    frame_bytes = height * width * 4
    identical = True
    started = time.perf_counter()
    for path in files:
        payload = process.stdout.read(frame_bytes)
        if len(payload) != frame_bytes:
            process.kill()
            raise RuntimeError("FFmpeg returned an incomplete decoded frame")
        decoded_bits = np.frombuffer(payload, dtype="<u4").reshape(shape)
        source_bits = read_float32_le(path, shape).view("<u4")
        if not np.array_equal(decoded_bits, source_bits):
            identical = False
            process.kill()
            break
    process.stdout.close()
    return_code = process.wait()
    if identical and return_code:
        raise RuntimeError(f"FFmpeg decoding failed with exit code {return_code}")
    return time.perf_counter() - started, identical


def write_report(
    path: Path, count: int, shape: tuple[int, int], fps: float, raw_bytes: int,
    tiff_bytes: int, grib_bytes: int | None, results: list[Result],
) -> None:
    lines = [
        "# ERA5 float32 byte-channel FFV1 report", "",
        "Each little-endian float32 value is mapped byte-for-byte to one BGRA pixel: B stores bits "
        "0-7, G stores 8-15, R stores 16-23, and A stores 24-31. FFV1 compresses these four "
        "8-bit channels losslessly in a Matroska container.", "",
        f"- Frames: {count} at {fps:g} fps", f"- Grid: {shape[0]} x {shape[1]}",
        f"- Raw float32 tensor: {raw_bytes:,} bytes", f"- Input TIFF directory: {tiff_bytes:,} bytes",
        f"- Input GRIB: {grib_bytes:,} bytes" if grib_bytes is not None else "- Input GRIB: unavailable for limited run",
        "", "| Profile | MKV bytes | Raw ratio | TIFF ratio | GRIB ratio | Encode s | MiB/s | Verify s | Bitwise |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for result in results:
        grib = f"{result.grib_ratio:.3f}x" if result.grib_ratio is not None else "n/a"
        lines.append(
            f"| {result.profile} | {result.output_bytes:,} | {result.raw_float32_ratio:.3f}x | "
            f"{result.tiff_ratio:.3f}x | {grib} | {result.encode_seconds:.2f} | "
            f"{result.encode_mib_s:.2f} | {result.verify_seconds:.2f} | "
            f"{'yes' if result.bitwise_identical else 'NO'} |"
        )
    lines += [
        "", "`ratio = source bytes / MKV bytes`; values above 1 mean the FFV1 file is smaller.", "",
        "This representation preserves float32 bits but is a custom scientific-data convention, not "
        "a standard semantic mapping understood by ordinary video players. Decoding must preserve BGRA "
        "and reconstruct little-endian float32 values.", "",
        "Machine-readable results are stored in `results.json` beside the MKV files.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.fps <= 0:
        raise ValueError("--fps must be positive")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("ffmpeg and ffprobe are required")
    files = input_files(args.input_dir, args.limit)
    first = np.asarray(tifffile.imread(files[0]), dtype="<f4")
    if first.ndim != 2:
        raise ValueError(f"Expected 2D TIFF, got {first.shape}")
    shape = tuple(int(value) for value in first.shape)
    raw_bytes = len(files) * first.size * 4
    tiff_bytes = sum(path.stat().st_size for path in files)
    grib_bytes = args.grib.stat().st_size if args.grib.is_file() and args.limit is None else None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    for profile in args.profiles:
        output = args.output_dir / f"{profile}.mkv"
        if output.exists() and not args.overwrite:
            raise FileExistsError(f"{output} exists; use --overwrite")
        temporary = output.with_suffix(".mkv.tmp")
        if temporary.exists():
            temporary.unlink()
        print(f"Encoding {profile} -> {output}", flush=True)
        try:
            encode_seconds = encode(files, shape, args.fps, profile, temporary)
            verify_seconds, identical = verify(temporary, files, shape)
            if not identical:
                raise RuntimeError(f"Bitwise verification failed for {profile}")
            temporary.replace(output)
        except BaseException:
            if temporary.exists():
                temporary.unlink()
            raise
        size = output.stat().st_size
        result = Result(
            profile, output.name, size, raw_bytes / size, tiff_bytes / size,
            grib_bytes / size if grib_bytes is not None else None,
            encode_seconds, raw_bytes / (1024**2) / encode_seconds, verify_seconds, identical,
        )
        results.append(result)
        print(f"  {size:,} bytes; verified={identical}", flush=True)
    write_report(args.report, len(files), shape, args.fps, raw_bytes, tiff_bytes, grib_bytes, results)
    metadata = {
        "shape": [len(files), *shape], "fps": args.fps, "pixel_format": "bgra",
        "byte_layout": {"B": "float bits 0-7", "G": "8-15", "R": "16-23", "A": "24-31"},
        "endianness": "little", "raw_float32_bytes": raw_bytes, "tiff_bytes": tiff_bytes,
        "grib_bytes": grib_bytes, "results": [asdict(result) for result in results],
    }
    (args.output_dir / "results.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
