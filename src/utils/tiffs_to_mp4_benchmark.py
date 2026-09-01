"""Encode an ERA5 float32 TIFF time series as MP4 and measure the trade-offs."""

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
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_mp4_benchmark"
DEFAULT_REPORT = PROJECT_ROOT / "Mp4Report.md"
DEFAULT_GRIB = PROJECT_ROOT / "data" / "ERA5-temperature-May2026.grib"

# name: (FFmpeg encoder, CRF, preset)
PROFILES = {
    "h264_crf18": ("libx264", 18, "medium"),
    "h264_crf23": ("libx264", 23, "medium"),
    "h264_crf28": ("libx264", 28, "medium"),
    "h265_crf28": ("libx265", 28, "medium"),
}


@dataclass(frozen=True)
class Result:
    profile: str
    codec: str
    crf: int
    output: str
    output_bytes: int
    encode_seconds: float
    encode_fps: float
    grib_ratio: float | None
    tiff_ratio: float
    raw_float32_ratio: float
    mae_kelvin: float
    rmse_kelvin: float
    max_error_kelvin: float
    psnr_db: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
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
    files = sorted(p for p in path.glob("*.tif*") if p.is_file())
    files = files[:limit] if limit is not None else files
    if not files:
        raise FileNotFoundError(f"No TIFF files found in {path}")
    return files


def read_frame(path: Path, shape: tuple[int, int]) -> np.ndarray:
    frame = np.asarray(tifffile.imread(path), dtype=np.float32)
    if frame.shape != shape:
        raise ValueError(f"{path} has shape {frame.shape}, expected {shape}")
    if not np.isfinite(frame).all():
        raise ValueError(f"{path} contains non-finite values")
    return frame


def scan_range(files: list[Path]) -> tuple[tuple[int, int], float, float]:
    first = np.asarray(tifffile.imread(files[0]), dtype=np.float32)
    if first.ndim != 2:
        raise ValueError(f"Expected 2D TIFF, got {first.shape}")
    shape = tuple(int(v) for v in first.shape)
    low, high = float(first.min()), float(first.max())
    for path in files[1:]:
        frame = read_frame(path, shape)
        low = min(low, float(frame.min()))
        high = max(high, float(frame.max()))
    if not high > low:
        raise ValueError("Dataset temperature range is zero")
    return shape, low, high


def quantize(frame: np.ndarray, low: float, high: float) -> np.ndarray:
    scaled = (frame - low) * (255.0 / (high - low))
    return np.rint(np.clip(scaled, 0, 255)).astype(np.uint8)


def encode(
    files: list[Path], shape: tuple[int, int], low: float, high: float,
    fps: float, profile: str, output: Path,
) -> float:
    height, width = shape
    codec, crf, preset = PROFILES[profile]
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
        "-r", str(fps), "-i", "pipe:0", "-an",
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-c:v", codec, "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
    ]
    started = time.perf_counter()
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for path in files:
            process.stdin.write(quantize(read_frame(path, shape), low, high).tobytes())
        process.stdin.close()
        return_code = process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    if return_code:
        raise RuntimeError(f"FFmpeg encoding failed with exit code {return_code}")
    return time.perf_counter() - started


def reconstruction_error(
    output: Path, files: list[Path], shape: tuple[int, int], low: float, high: float,
) -> tuple[float, float, float, float]:
    height, width = shape
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(output),
        "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    assert process.stdout is not None
    padded_height = height + height % 2
    frame_bytes = width * padded_height
    absolute_sum = squared_sum = max_error = 0.0
    value_count = 0
    scale = (high - low) / 255.0
    for path in files:
        payload = process.stdout.read(frame_bytes)
        if len(payload) != frame_bytes:
            process.kill()
            raise RuntimeError("FFmpeg decoder returned an incomplete frame")
        decoded = np.frombuffer(payload, np.uint8).reshape(padded_height, width)[:height]
        restored = decoded.astype(np.float32) * scale + low
        difference = restored.astype(np.float64) - read_frame(path, shape)
        absolute_sum += float(np.abs(difference).sum())
        squared_sum += float(np.square(difference).sum())
        max_error = max(max_error, float(np.abs(difference).max()))
        value_count += difference.size
    process.stdout.close()
    if process.wait() != 0:
        raise RuntimeError("FFmpeg decoding failed")
    mae = absolute_sum / value_count
    rmse = math.sqrt(squared_sum / value_count)
    psnr = 20.0 * math.log10((high - low) / rmse) if rmse else math.inf
    return mae, rmse, max_error, psnr


def write_report(
    path: Path, files: list[Path], shape: tuple[int, int], low: float, high: float,
    fps: float, tiff_bytes: int, grib_bytes: int | None, results: list[Result],
) -> None:
    raw_bytes = len(files) * shape[0] * shape[1] * 4
    lines = [
        "# ERA5 to MP4 compression report", "",
        "The temperature fields were mapped over one global dataset range to 8-bit grayscale, "
        "then encoded as YUV 4:2:0 MP4. This is a lossy representation.", "",
        f"- Frames: {len(files)} at {fps:g} fps", f"- Grid: {shape[0]} x {shape[1]}",
        f"- Temperature range: {low:.6f} to {high:.6f} K ({high-low:.6f} K span)",
        f"- Raw float32 tensor: {raw_bytes:,} bytes", f"- Input TIFF files: {tiff_bytes:,} bytes",
        f"- Input GRIB: {grib_bytes:,} bytes" if grib_bytes is not None else "- Input GRIB: unavailable",
        "", "| Profile | MP4 bytes | GRIB ratio | TIFF ratio | raw-f32 ratio | Encode s | fps | MAE K | RMSE K | Max K | PSNR dB |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        grib = f"{r.grib_ratio:.2f}x" if r.grib_ratio is not None else "n/a"
        lines.append(
            f"| {r.profile} | {r.output_bytes:,} | {grib} | {r.tiff_ratio:.2f}x | "
            f"{r.raw_float32_ratio:.2f}x | {r.encode_seconds:.2f} | {r.encode_fps:.2f} | "
            f"{r.mae_kelvin:.4f} | {r.rmse_kelvin:.4f} | {r.max_error_kelvin:.4f} | {r.psnr_db:.2f} |"
        )
    lines += ["", "`ratio = source bytes / MP4 bytes`; larger is more compression.", "",
              "Machine-readable results are in `results.json` beside the MP4 files."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.fps <= 0:
        raise ValueError("--fps must be positive")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not available")
    files = input_files(args.input_dir, args.limit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    targets = {name: args.output_dir / f"{name}.mp4" for name in args.profiles}
    existing = [path for path in [*targets.values(), args.report] if path.exists()]
    if existing and not args.overwrite:
        raise FileExistsError(f"{existing[0]} exists; use --overwrite")

    shape, low, high = scan_range(files)
    tiff_bytes = sum(path.stat().st_size for path in files)
    grib_bytes = args.grib.stat().st_size if args.grib.is_file() and args.limit is None else None
    raw_bytes = len(files) * shape[0] * shape[1] * 4
    results: list[Result] = []
    for profile in args.profiles:
        output = targets[profile]
        print(f"Encoding {profile} -> {output}", flush=True)
        elapsed = encode(files, shape, low, high, args.fps, profile, output)
        mae, rmse, maximum, psnr = reconstruction_error(output, files, shape, low, high)
        size = output.stat().st_size
        codec, crf, _ = PROFILES[profile]
        results.append(Result(
            profile, codec, crf, output.name, size, elapsed, len(files) / elapsed,
            grib_bytes / size if grib_bytes is not None else None,
            tiff_bytes / size, raw_bytes / size, mae, rmse, maximum, psnr,
        ))
        print(f"  {size:,} bytes, RMSE={rmse:.4f} K", flush=True)

    write_report(args.report, files, shape, low, high, args.fps, tiff_bytes, grib_bytes, results)
    metadata = {
        "frame_count": len(files), "shape": shape, "fps": args.fps,
        "temperature_min_kelvin": low, "temperature_max_kelvin": high,
        "tiff_bytes": tiff_bytes, "grib_bytes": grib_bytes, "raw_float32_bytes": raw_bytes,
        "results": [asdict(result) for result in results],
    }
    (args.output_dir / "results.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
