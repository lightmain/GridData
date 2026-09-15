"""Application-layer, lossless 3-D delta benchmarks for Zarr and HDF5.

Each storage chunk is transformed independently so it remains randomly
decodable. Float32 values are viewed as uint32 words; differencing and inverse
prefix sums use arithmetic modulo 2**32 and are therefore bit-exact for every
possible float32 bit pattern.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import h5py
import hdf5plugin
import matplotlib
import numpy as np
import tifffile
import zarr
from numcodecs import Blosc

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ORIGINAL = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_NOISE_ROOT = PROJECT_ROOT / "data" / "3-dim-diff-noise-container-benchmark"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "3-dim-diff-zarr-hdf5"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "ThreeDimDiffZarrHdf5-cn.md"
DEFAULT_FIGURES = PROJECT_ROOT / "reports" / "figures" / "3-dim-diff-zarr-hdf5"
DEFAULT_DIFF_RESULTS = PROJECT_ROOT / "data" / "3-dim-diff-noise" / "results.json"
DEFAULT_CHUNKS = (24, 128, 256)
SWEEP_CHUNKS = ((48, 128, 256), (96, 128, 256))
VARIANTS = ("original", "noise7", "noise8")
VARIANT_LABELS = {"original": "原始", "noise7": "低 7 位白化", "noise8": "低 8 位白化"}


@dataclass(frozen=True)
class Mode:
    name: str
    delta: bool
    zigzag: bool
    reorder: bool
    bitshuffle: bool


MODES = (
    Mode("raw_bitshuffle", False, False, False, True),
    Mode("delta_noshuffle", True, False, False, False),
    Mode("delta_bitshuffle", True, False, False, True),
    Mode("delta_zigzag_noshuffle", True, True, False, False),
    Mode("delta_zigzag_bitshuffle", True, True, False, True),
    Mode("delta_zigzag_reorder_bitshuffle", True, True, True, True),
)
MODE_BY_NAME = {mode.name: mode for mode in MODES}


def parse_chunk(value: str) -> tuple[int, int, int]:
    result = tuple(int(part) for part in value.split(","))
    if len(result) != 3 or any(part < 1 for part in result):
        raise argparse.ArgumentTypeError("chunk must be t,y,x with three positive integers")
    return result  # type: ignore[return-value]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, default=DEFAULT_ORIGINAL)
    parser.add_argument("--noise-root", type=Path, default=DEFAULT_NOISE_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--figures", type=Path, default=DEFAULT_FIGURES)
    parser.add_argument("--diff-results", type=Path, default=DEFAULT_DIFF_RESULTS)
    parser.add_argument("--chunks", type=parse_chunk, default=DEFAULT_CHUNKS)
    parser.add_argument("--sweep-chunks", type=parse_chunk, nargs="*", default=list(SWEEP_CHUNKS))
    parser.add_argument("--modes", nargs="+", choices=tuple(MODE_BY_NAME), default=list(MODE_BY_NAME))
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--progress-every", type=int, default=24)
    return parser.parse_args()


def tiff_files(path: Path, limit: int | None = None) -> list[Path]:
    files = sorted(path.glob("*.tif*"))
    if limit is not None:
        files = files[:limit]
    if not files:
        raise FileNotFoundError(f"No TIFF files in {path}")
    return files


def input_files(args: argparse.Namespace) -> dict[str, list[Path]]:
    paths = {
        "original": args.original,
        "noise7": args.noise_root / "noise7" / "tiffs",
        "noise8": args.noise_root / "noise8" / "tiffs",
    }
    result = {name: tiff_files(paths[name], args.limit) for name in args.variants}
    expected = len(next(iter(result.values())))
    for name, files in result.items():
        if len(files) != expected:
            raise ValueError(f"Variant {name} has {len(files)} TIFFs, expected {expected}")
    return result


def read_float(path: Path) -> np.ndarray:
    values = tifffile.imread(path).astype("<f4", copy=False)
    if values.ndim != 2:
        raise ValueError(f"Expected 2-D TIFF, got {values.shape}: {path}")
    return values


def read_slab(files: list[Path]) -> np.ndarray:
    first = read_float(files[0])
    slab = np.empty((len(files), *first.shape), dtype="<f4")
    slab[0] = first
    for index, path in enumerate(files[1:], start=1):
        current = read_float(path)
        if current.shape != first.shape:
            raise ValueError(f"Shape mismatch: {path}: {current.shape} != {first.shape}")
        slab[index] = current
    return slab


def axis_tail_slices(ndim: int, axis: int) -> tuple[tuple[slice, ...], tuple[slice, ...]]:
    current = [slice(None)] * ndim
    previous = [slice(None)] * ndim
    current[axis] = slice(1, None)
    previous[axis] = slice(None, -1)
    return tuple(current), tuple(previous)


def modular_delta(words: np.ndarray) -> np.ndarray:
    result = np.asarray(words, dtype="<u4").copy()
    for axis in (2, 1, 0):
        current, previous = axis_tail_slices(result.ndim, axis)
        result[current] = result[current] - result[previous]
    return result


def inverse_modular_delta(delta: np.ndarray) -> np.ndarray:
    result = np.asarray(delta, dtype="<u4").copy()
    for axis in (0, 1, 2):
        np.add.accumulate(result, axis=axis, dtype=np.uint32, out=result)
    return result


def zigzag_encode(words: np.ndarray) -> np.ndarray:
    unsigned = np.asarray(words, dtype="<u4")
    sign_mask = np.uint32(0) - (unsigned >> np.uint32(31))
    return ((unsigned << np.uint32(1)) ^ sign_mask).astype("<u4", copy=False)


def zigzag_decode(encoded: np.ndarray) -> np.ndarray:
    value = np.asarray(encoded, dtype="<u4")
    sign_mask = np.uint32(0) - (value & np.uint32(1))
    return ((value >> np.uint32(1)) ^ sign_mask).astype("<u4", copy=False)


def region_views(values: np.ndarray) -> tuple[np.ndarray, ...]:
    return (
        values[1:, 1:, 1:],
        values[0, 1:, 1:],
        values[1:, 0, 1:],
        values[1:, 1:, 0],
        values[0, 0, 1:],
        values[0, 1:, 0],
        values[1:, 0, 0],
        values[0:1, 0:1, 0:1],
    )


def reorder_regions(values: np.ndarray) -> np.ndarray:
    packed = np.concatenate([part.reshape(-1) for part in region_views(values)])
    if packed.size != values.size:
        raise AssertionError("Region packing did not partition the chunk")
    return packed.reshape(values.shape)


def undo_region_reorder(packed: np.ndarray) -> np.ndarray:
    shape = packed.shape
    result = np.empty(shape, dtype=packed.dtype)
    source = packed.reshape(-1)
    offset = 0
    for target in region_views(result):
        size = target.size
        target[...] = source[offset:offset + size].reshape(target.shape)
        offset += size
    if offset != source.size:
        raise AssertionError("Region unpacking did not consume the chunk")
    return result


def encode_block(float_block: np.ndarray, mode: Mode) -> np.ndarray:
    if not mode.delta:
        return np.asarray(float_block, dtype="<f4")
    encoded = modular_delta(np.asarray(float_block, dtype="<f4").view("<u4"))
    if mode.zigzag:
        encoded = zigzag_encode(encoded)
    if mode.reorder:
        encoded = reorder_regions(encoded)
    return encoded


def decode_block(stored: np.ndarray, mode: Mode) -> np.ndarray:
    if not mode.delta:
        return np.asarray(stored, dtype="<f4").view("<u4")
    encoded = np.asarray(stored, dtype="<u4")
    if mode.reorder:
        encoded = undo_region_reorder(encoded)
    if mode.zigzag:
        encoded = zigzag_decode(encoded)
    return inverse_modular_delta(encoded)


def validate_transforms() -> None:
    rng = np.random.default_rng(2311)
    cases = [
        rng.integers(0, 2**32, size=(5, 7, 11), dtype=np.uint32),
        np.asarray([0, 1, 2**31 - 1, 2**31, 2**32 - 1], dtype=np.uint32).reshape(1, 1, 5),
    ]
    for words in cases:
        floats = words.astype("<u4", copy=False).view("<f4")
        for mode in MODES:
            decoded = decode_block(encode_block(floats, mode), mode)
            if not np.array_equal(decoded, words):
                raise AssertionError(f"Transform roundtrip failed: {mode.name}")


def remove_exact(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def directory_size(path: Path) -> int:
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def chunk_label(chunks: tuple[int, int, int]) -> str:
    return "x".join(str(value) for value in chunks)


def slices_for(shape: tuple[int, int, int], chunks: tuple[int, int, int]) -> Iterable[tuple[slice, slice, slice]]:
    for t in range(0, shape[0], chunks[0]):
        for y in range(0, shape[1], chunks[1]):
            for x in range(0, shape[2], chunks[2]):
                yield (
                    slice(t, min(t + chunks[0], shape[0])),
                    slice(y, min(y + chunks[1], shape[1])),
                    slice(x, min(x + chunks[2], shape[2])),
                )


def compressor_pair(mode: Mode) -> tuple[Blosc, dict]:
    zarr_shuffle = Blosc.BITSHUFFLE if mode.bitshuffle else Blosc.NOSHUFFLE
    hdf_shuffle = hdf5plugin.Blosc.BITSHUFFLE if mode.bitshuffle else hdf5plugin.Blosc.NOSHUFFLE
    return (
        Blosc(cname="zstd", clevel=5, shuffle=zarr_shuffle),
        hdf5plugin.Blosc(cname="zstd", clevel=5, shuffle=hdf_shuffle),
    )


def create_arrays(
    zarr_path: Path,
    hdf5_path: Path,
    shape: tuple[int, int, int],
    chunks: tuple[int, int, int],
    mode: Mode,
    overwrite: bool,
) -> tuple[zarr.Array, h5py.File, h5py.Dataset]:
    for path in (zarr_path, hdf5_path):
        if path.exists():
            if not overwrite:
                raise FileExistsError(f"{path} exists; pass --overwrite")
            remove_exact(path)
    zarr_path.parent.mkdir(parents=True, exist_ok=True)
    hdf5_path.parent.mkdir(parents=True, exist_ok=True)
    effective_chunks = tuple(min(a, b) for a, b in zip(chunks, shape))
    dtype = "<u4" if mode.delta else "<f4"
    zarr_compressor, hdf_compression = compressor_pair(mode)
    metadata = {
        "transform": "chunk-local-modular-3d-delta" if mode.delta else "identity",
        "transform_version": 1,
        "original_dtype": "<f4",
        "encoded_dtype": dtype,
        "axis_order": ["time", "latitude", "longitude"],
        "arithmetic": "modulo-2^32" if mode.delta else "none",
        "zigzag_int32": mode.zigzag,
        "region_reorder": mode.reorder,
        "bitshuffle": mode.bitshuffle,
        "codec": "Blosc Zstd level 5",
        "logical_chunks": list(effective_chunks),
    }
    zarray = zarr.open_array(
        str(zarr_path), mode="w", shape=shape, chunks=effective_chunks, dtype=dtype,
        compressor=zarr_compressor, fill_value=0, zarr_format=2,
    )
    zarray.attrs.update(metadata)
    hfile = h5py.File(hdf5_path, "w")
    hdataset = hfile.create_dataset(
        "t2m_encoded", shape=shape, chunks=effective_chunks, dtype=dtype,
        fillvalue=0, **hdf_compression,
    )
    for key, value in metadata.items():
        hdataset.attrs[key] = json.dumps(value) if isinstance(value, (list, dict)) else value
    return zarray, hfile, hdataset


def run_one(
    variant: str,
    files: list[Path],
    output: Path,
    chunks: tuple[int, int, int],
    mode: Mode,
    overwrite: bool,
    progress_every: int,
) -> dict:
    first = read_float(files[0])
    shape = (len(files), *first.shape)
    effective_chunks = tuple(min(a, b) for a, b in zip(chunks, shape))
    base = output / variant / chunk_label(chunks)
    zarr_path = base / f"{mode.name}.zarr"
    hdf5_path = base / f"{mode.name}.h5"
    zarray, hfile, hdataset = create_arrays(
        zarr_path, hdf5_path, shape, chunks, mode, overwrite
    )
    started = time.perf_counter()
    try:
        for t_start in range(0, shape[0], effective_chunks[0]):
            t_stop = min(t_start + effective_chunks[0], shape[0])
            slab = read_slab(files[t_start:t_stop])
            for y_start in range(0, shape[1], effective_chunks[1]):
                y_stop = min(y_start + effective_chunks[1], shape[1])
                for x_start in range(0, shape[2], effective_chunks[2]):
                    x_stop = min(x_start + effective_chunks[2], shape[2])
                    selection = (
                        slice(t_start, t_stop), slice(y_start, y_stop), slice(x_start, x_stop)
                    )
                    block = slab[:, y_start:y_stop, x_start:x_stop]
                    encoded = encode_block(block, mode)
                    zarray[selection] = encoded
                    hdataset[selection] = encoded
            if progress_every and (t_stop % progress_every == 0 or t_stop == shape[0]):
                print(
                    f"write {variant} {chunk_label(chunks)} {mode.name}: {t_stop}/{shape[0]}",
                    flush=True,
                )
    finally:
        hfile.close()
    write_seconds = time.perf_counter() - started

    verify_started = time.perf_counter()
    hdf5_verified = True
    zarr_verified = True
    with h5py.File(hdf5_path, "r") as handle:
        dataset = handle["t2m_encoded"]
        for t_start in range(0, shape[0], effective_chunks[0]):
            t_stop = min(t_start + effective_chunks[0], shape[0])
            slab = read_slab(files[t_start:t_stop])
            for y_start in range(0, shape[1], effective_chunks[1]):
                y_stop = min(y_start + effective_chunks[1], shape[1])
                for x_start in range(0, shape[2], effective_chunks[2]):
                    x_stop = min(x_start + effective_chunks[2], shape[2])
                    selection = (
                        slice(t_start, t_stop), slice(y_start, y_stop), slice(x_start, x_stop)
                    )
                    expected = slab[:, y_start:y_stop, x_start:x_stop].view("<u4")
                    zarr_decoded = decode_block(np.asarray(zarray[selection]), mode)
                    hdf5_decoded = decode_block(np.asarray(dataset[selection]), mode)
                    if not np.array_equal(zarr_decoded, expected):
                        zarr_verified = False
                        raise AssertionError(f"Zarr roundtrip failed: {variant}, {mode.name}, {selection}")
                    if not np.array_equal(hdf5_decoded, expected):
                        hdf5_verified = False
                        raise AssertionError(f"HDF5 roundtrip failed: {variant}, {mode.name}, {selection}")
    verify_seconds = time.perf_counter() - verify_started
    count = math.prod(shape)
    zarr_bytes = directory_size(zarr_path)
    hdf5_bytes = hdf5_path.stat().st_size
    return {
        "variant": variant,
        "chunks": list(chunks),
        "effective_chunks": list(effective_chunks),
        "mode": mode.name,
        "delta": mode.delta,
        "zigzag": mode.zigzag,
        "region_reorder": mode.reorder,
        "bitshuffle": mode.bitshuffle,
        "write_seconds_both_formats": write_seconds,
        "verify_seconds_both_formats": verify_seconds,
        "zarr": {
            "path": str(zarr_path.resolve()), "size_bytes": zarr_bytes,
            "bits_per_value": zarr_bytes * 8 / count, "bitwise_verified": zarr_verified,
        },
        "hdf5": {
            "path": str(hdf5_path.resolve()), "size_bytes": hdf5_bytes,
            "bits_per_value": hdf5_bytes * 8 / count, "bitwise_verified": hdf5_verified,
        },
    }


def diff_statistics(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "weighted_delta_entropy": {
            name: float(data["weighted_delta_entropy"][name]) for name in VARIANTS
        },
        "raw_word_entropy": {
            name: float(data["distributions"][f"{name}_raw"]["entropy_bits_per_value"])
            for name in VARIANTS
        },
    }


def record_key(record: dict) -> tuple[str, tuple[int, ...], str]:
    return record["variant"], tuple(record["chunks"]), record["mode"]


def choose_sweep_mode(records: list[dict], default_chunks: tuple[int, int, int]) -> str:
    candidates: dict[str, list[float]] = {}
    for record in records:
        if tuple(record["chunks"]) != default_chunks or not record["delta"]:
            continue
        candidates.setdefault(record["mode"], []).extend(
            [record["zarr"]["bits_per_value"], record["hdf5"]["bits_per_value"]]
        )
    if not candidates:
        raise ValueError("No transformed default-chunk records available for sweep")
    return min(candidates, key=lambda name: sum(candidates[name]) / len(candidates[name]))


def configure_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
        "axes.unicode_minus": False, "figure.dpi": 130, "savefig.dpi": 180,
    })


def plot_results(result: dict, figures: Path) -> None:
    configure_style()
    figures.mkdir(parents=True, exist_ok=True)
    records = result["records"]
    default = tuple(result["default_chunks"])
    mode_names = [name for name in result["default_modes"]]
    short = {
        "raw_bitshuffle": "raw\nbitshuffle",
        "delta_noshuffle": "delta",
        "delta_bitshuffle": "delta\nbitshuffle",
        "delta_zigzag_noshuffle": "delta\nzigzag",
        "delta_zigzag_bitshuffle": "delta+zigzag\nbitshuffle",
        "delta_zigzag_reorder_bitshuffle": "delta+zigzag\nreorder+bitshuffle",
    }
    fig, axes = plt.subplots(1, len(result["variants"]), figsize=(18, 5.4), sharey=True, constrained_layout=True)
    if not isinstance(axes, np.ndarray):
        axes = np.asarray([axes])
    for ax, variant in zip(axes, result["variants"], strict=True):
        subset = {r["mode"]: r for r in records if r["variant"] == variant and tuple(r["chunks"]) == default}
        x = np.arange(len(mode_names))
        zvalues = [subset[name]["zarr"]["bits_per_value"] for name in mode_names]
        hvalues = [subset[name]["hdf5"]["bits_per_value"] for name in mode_names]
        ax.bar(x - .19, zvalues, .38, label="Zarr", color="#059669")
        ax.bar(x + .19, hvalues, .38, label="HDF5", color="#d97706")
        ax.axhline(result["statistics"]["weighted_delta_entropy"][variant], color="black", linestyle="--", linewidth=1.2, label="全局差分边际熵")
        ax.set_xticks(x, [short[name] for name in mode_names], rotation=24, ha="right")
        ax.set_title(VARIANT_LABELS[variant])
        ax.set_ylabel("bit / value")
    axes[0].legend(fontsize=8)
    fig.suptitle(f"应用层三维差分消融，chunk={default}")
    fig.savefig(figures / "transform_ablation.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    sweep_mode = result["sweep_mode"]
    chunk_values = [tuple(result["default_chunks"]), *[tuple(c) for c in result["sweep_chunks"]]]
    fig, axes = plt.subplots(1, len(result["variants"]), figsize=(15, 4.8), sharey=False, constrained_layout=True)
    if not isinstance(axes, np.ndarray):
        axes = np.asarray([axes])
    for ax, variant in zip(axes, result["variants"], strict=True):
        subset = {(tuple(r["chunks"]), r["mode"]): r for r in records if r["variant"] == variant}
        labels, zvalues, hvalues = [], [], []
        for chunks in chunk_values:
            record = subset[(chunks, sweep_mode)]
            labels.append(chunk_label(chunks))
            zvalues.append(record["zarr"]["bits_per_value"])
            hvalues.append(record["hdf5"]["bits_per_value"])
        ax.plot(labels, zvalues, "o-", label="Zarr")
        ax.plot(labels, hvalues, "s-", label="HDF5")
        low, high = min(zvalues + hvalues), max(zvalues + hvalues)
        padding = max((high - low) * 0.25, 0.002)
        ax.set_ylim(low - padding, high + padding)
        ax.set_title(VARIANT_LABELS[variant])
        ax.set_xlabel("chunk")
        ax.tick_params(axis="x", rotation=20)
        ax.set_ylabel("bit / value")
    axes[0].legend()
    fig.suptitle(f"最佳变换 `{sweep_mode}` 的时间 chunk 扫描")
    fig.savefig(figures / "chunk_sweep.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_report(path: Path, result: dict) -> None:
    records = result["records"]
    default = tuple(result["default_chunks"])
    default_records = [r for r in records if tuple(r["chunks"]) == default]
    baselines = {
        (r["variant"], fmt): r[fmt]["bits_per_value"]
        for r in default_records if r["mode"] == "raw_bitshuffle" for fmt in ("zarr", "hdf5")
    }
    rows = []
    for variant in result["variants"]:
        for mode in result["default_modes"]:
            record = next(r for r in default_records if r["variant"] == variant and r["mode"] == mode)
            z = record["zarr"]["bits_per_value"]
            h = record["hdf5"]["bits_per_value"]
            rows.append(
                f"| {VARIANT_LABELS[variant]} | `{mode}` | {z:.4f} | {h:.4f} | "
                f"{baselines[(variant, 'zarr')] - z:+.4f} | {baselines[(variant, 'hdf5')] - h:+.4f} |"
            )
    sweep_rows = []
    sweep_mode = result["sweep_mode"]
    for variant in result["variants"]:
        for chunks in [default, *[tuple(c) for c in result["sweep_chunks"]]]:
            record = next(r for r in records if r["variant"] == variant and tuple(r["chunks"]) == chunks and r["mode"] == sweep_mode)
            boundary = 100 * (1 - math.prod(v - 1 for v in chunks) / math.prod(chunks))
            sweep_rows.append(
                f"| {VARIANT_LABELS[variant]} | `{chunks}` | {boundary:.3f}% | "
                f"{record['zarr']['bits_per_value']:.4f} | {record['hdf5']['bits_per_value']:.4f} |"
            )
    best_lines = []
    for variant in result["variants"]:
        for fmt in ("zarr", "hdf5"):
            candidate = min((r for r in records if r["variant"] == variant), key=lambda r: r[fmt]["bits_per_value"])
            baseline = baselines[(variant, fmt)]
            rate = candidate[fmt]["bits_per_value"]
            size_bytes = candidate[fmt]["size_bytes"]
            best_lines.append(
                f"- {VARIANT_LABELS[variant]} {fmt.upper()}：最佳 `{candidate['mode']}` / "
                f"`{tuple(candidate['chunks'])}` 为 `{size_bytes / 2**20:.2f}` MiB、"
                f"`{rate:.4f}` bit/value、相对 float32 为 `{32 / rate:.3f}×`，相对 raw baseline "
                f"减少 `{baseline - rate:.4f}` bit/value（`{100 * (baseline - rate) / baseline:.2f}%`）。"
            )
    original_default = [
        r for r in default_records if r["variant"] == "original"
    ]
    timing_rows = [
        f"| `{r['mode']}` | {r['write_seconds_both_formats']:.2f} | "
        f"{r['verify_seconds_both_formats']:.2f} |"
        for r in original_default
    ]
    lines = [
        "# 三维差分在 Zarr/HDF5 中的应用层预变换实测", "",
        "## 实验设计", "",
        "每个存储 chunk 独立执行可逆变换，保证随机读取时只需解码目标 chunk。float32 被按位解释为",
        "uint32，三维差分与逆累加均在模 2^32 算术下进行。所有结果均逐 chunk 解码，并与输入",
        "float32 位模式逐元素比较。", "",
        "两种格式均使用 Blosc-Zstd level 5；带 bitshuffle 的模式在两种格式中使用相同过滤器。",
        f"张量形状：`{tuple(result['shape'])}`，每组 `{result['value_count']:,}` 个值。", "",
        "## 默认 chunk 的变换消融", "",
        f"默认 chunk：`{default}`。正的改善量表示比同格式 raw float32 baseline 更小。", "",
        "| 数据 | 模式 | Zarr bit/value | HDF5 bit/value | Zarr 改善 | HDF5 改善 |",
        "| --- | --- | ---: | ---: | ---: | ---: |", *rows, "",
        "## Chunk 扫描", "",
        f"默认消融后，跨两种格式和三组数据平均最好的差分模式为 `{sweep_mode}`。", "",
        "| 数据 | chunk | chunk 内边界比例 | Zarr bit/value | HDF5 bit/value |",
        "| --- | --- | ---: | ---: | ---: |", *sweep_rows, "",
        "## 最佳实际文件", "", *best_lines, "",
        "原始数据最佳 Zarr 文件比当前 raw Zarr baseline 实际减少约 "
        f"`{(baselines[('original', 'zarr')] - min(r['zarr']['bits_per_value'] for r in records if r['variant'] == 'original')) / 8 * result['value_count'] / 2**20:.2f}` MiB。", "",
        "## 时间开销", "",
        "下表为默认 chunk、原始数据的时间；写入时间包含同一次遍历中同时写 Zarr 与 HDF5，",
        "验证时间包含两种格式的逐 chunk 解码和位级比较。", "",
        "| 模式 | 双格式写入（秒） | 双格式验证（秒） |",
        "| --- | ---: | ---: |", *timing_rows, "",
        "## 判读原则", "",
        "- `raw_bitshuffle` 与此前通用容器基线使用同一核心配置，因此改善量是实际文件大小差，不是熵差。",
        "- `delta_noshuffle` 与 `delta_bitshuffle` 分离 bitshuffle 的作用；ZigZag 模式检验正负小残差映射",
        "  是否有利于 Zstd；region reorder 检验将内部、三个平面、三条棱和顶点连续排列是否有效。",
        "- chunk 内边界比全局变换更多，所以实际码率不应直接等于 6.83 bit/value 的全局边际熵。",
        "- 只有实际文件小于 raw baseline 且逐位恢复通过，才能认为这一应用层方案获得了真实无损收益。", "",
        "## 兼容性限制", "",
        "当前文件是应用层预变换原型：数据集物理 dtype 为 uint32，普通 Zarr/HDF5 客户端读到的是编码残差，",
        "必须通过本项目的逆变换才能得到 float32 温度。要做到对 xarray/h5py 用户透明，下一阶段需要把相同",
        "变换包装成 Zarr codec 或 HDF5 filter plugin。", "",
        "## 图表", "",
        "![变换消融](figures/3-dim-diff-zarr-hdf5/transform_ablation.png)", "",
        "![chunk 扫描](figures/3-dim-diff-zarr-hdf5/chunk_sweep.png)", "",
        "完整机器可读结果位于 `data/3-dim-diff-zarr-hdf5/results.json`。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    validate_transforms()
    files_by_variant = input_files(args)
    first_files = next(iter(files_by_variant.values()))
    first = read_float(first_files[0])
    shape = (len(first_files), *first.shape)
    result = {
        "method": "application_layer_chunk_local_modular_3d_delta",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "shape": list(shape), "value_count": math.prod(shape),
        "default_chunks": list(args.chunks), "sweep_chunks": [list(c) for c in args.sweep_chunks],
        "default_modes": list(args.modes), "variants": list(args.variants),
        "statistics": diff_statistics(args.diff_results),
        "versions": {
            "python": platform.python_version(), "numpy": np.__version__,
            "tifffile": tifffile.__version__, "zarr": zarr.__version__, "h5py": h5py.__version__,
        },
        "records": [],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    partial = args.output / "results.partial.json"
    for variant in args.variants:
        for mode_name in args.modes:
            print(f"=== default {variant} {mode_name} ===", flush=True)
            result["records"].append(run_one(
                variant, files_by_variant[variant], args.output, args.chunks,
                MODE_BY_NAME[mode_name], args.overwrite, args.progress_every,
            ))
            partial.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["sweep_mode"] = choose_sweep_mode(result["records"], args.chunks)
    for chunks in args.sweep_chunks:
        if chunks == args.chunks:
            continue
        for variant in args.variants:
            print(f"=== sweep {variant} {chunk_label(chunks)} {result['sweep_mode']} ===", flush=True)
            result["records"].append(run_one(
                variant, files_by_variant[variant], args.output, chunks,
                MODE_BY_NAME[result["sweep_mode"]], args.overwrite, args.progress_every,
            ))
            partial.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result_path = args.output / "results.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if partial.exists():
        partial.unlink()
    plot_results(result, args.figures)
    write_report(args.report, result)
    print(f"Wrote {result_path}")
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
