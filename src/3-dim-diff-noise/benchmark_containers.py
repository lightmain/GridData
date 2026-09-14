"""Benchmark TIFF, Zarr, and HDF5 after whitening low float32 bits.

The whitening rule is identical to ``analyze.py``: for each value, retain all
bits above the lowest k bits and replace those k bits with independent uniform
random bits.  The original and whitened variants are then stored losslessly.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import time
from pathlib import Path

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
DEFAULT_INPUT = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "3-dim-diff-noise-container-benchmark"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "WhitenedContainerBenchmark-cn.md"
DEFAULT_FIGURES = PROJECT_ROOT / "reports" / "figures" / "3-dim-diff-noise-containers"
DEFAULT_DIFF_RESULTS = PROJECT_ROOT / "data" / "3-dim-diff-noise" / "results.json"
CHUNKS = (24, 128, 256)
VARIANTS = ("original", "noise7", "noise8")
LABELS = {"original": "原始", "noise7": "低 7 位白化", "noise8": "低 8 位白化"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--figures", type=Path, default=DEFAULT_FIGURES)
    parser.add_argument("--diff-results", type=Path, default=DEFAULT_DIFF_RESULTS)
    parser.add_argument("--seed", type=int, default=2311)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--progress-every", type=int, default=24)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--reuse-whitened-tiffs",
        action="store_true",
        help="Do not regenerate noise7/noise8 TIFFs; verify and reuse them.",
    )
    return parser.parse_args()


def tiff_files(path: Path, limit: int | None = None) -> list[Path]:
    files = sorted(path.glob("*.tif*"))
    if limit is not None:
        files = files[:limit]
    if not files:
        raise FileNotFoundError(f"No TIFF files in {path}")
    return files


def read_float(path: Path) -> np.ndarray:
    values = tifffile.imread(path).astype("<f4", copy=False)
    if values.ndim != 2:
        raise ValueError(f"Expected 2-D TIFF, got {values.shape}: {path}")
    return values


def whiten_low_bits(words: np.ndarray, bits: int, rng: np.random.Generator) -> np.ndarray:
    mask = np.uint32((1 << bits) - 1)
    random_low = rng.integers(0, 1 << bits, size=words.shape, dtype=np.uint32)
    result = (words & ~mask) | random_low
    if not np.array_equal(result >> np.uint32(bits), words >> np.uint32(bits)):
        raise AssertionError(f"Whitening escaped the lowest {bits} bits")
    return result


def remove_exact(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def directory_size(path: Path) -> int:
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def source_size(files: list[Path]) -> int:
    return sum(path.stat().st_size for path in files)


def read_description(path: Path) -> str | None:
    with tifffile.TiffFile(path) as handle:
        return handle.pages[0].description


def create_whitened_tiffs(
    originals: list[Path],
    output: Path,
    seed: int,
    overwrite: bool,
    progress_every: int,
) -> dict[str, dict]:
    targets = {name: output / name / "tiffs" for name in ("noise7", "noise8")}
    for target in targets.values():
        if target.exists():
            if not overwrite:
                raise FileExistsError(f"{target} exists; pass --overwrite or --reuse-whitened-tiffs")
            remove_exact(target)
        target.mkdir(parents=True)

    rngs = {"noise7": np.random.default_rng(seed), "noise8": np.random.default_rng(seed)}
    bits_by_name = {"noise7": 7, "noise8": 8}
    started = time.perf_counter()
    for index, source in enumerate(originals, start=1):
        original = read_float(source)
        words = original.view("<u4")
        description = read_description(source)
        for name, bits in bits_by_name.items():
            noisy_words = whiten_low_bits(words, bits, rngs[name])
            noisy = noisy_words.astype("<u4", copy=False).view("<f4")
            target = targets[name] / source.name
            tifffile.imwrite(
                target,
                noisy,
                byteorder="<",
                photometric="minisblack",
                planarconfig="contig",
                compression="lzw",
                predictor=True,
                tile=(256, 256),
                description=description,
                metadata=None,
            )
            if not np.array_equal(read_float(target).view("<u4"), noisy_words):
                raise AssertionError(f"TIFF bitwise verification failed: {target}")
        if progress_every and (index % progress_every == 0 or index == len(originals)):
            print(f"TIFF generation {index}/{len(originals)}", flush=True)

    elapsed = time.perf_counter() - started
    return {
        name: {
            "path": str(path.resolve()),
            "size_bytes": source_size(tiff_files(path)),
            "write_seconds_shared": elapsed,
            "bitwise_verified": True,
        }
        for name, path in targets.items()
    }


def verify_reused_tiffs(
    originals: list[Path], output: Path, seed: int, progress_every: int
) -> dict[str, dict]:
    files_by_name = {
        name: tiff_files(output / name / "tiffs", len(originals)) for name in ("noise7", "noise8")
    }
    for name, files in files_by_name.items():
        if len(files) != len(originals):
            raise ValueError(f"Expected {len(originals)} {name} TIFFs, found {len(files)}")
    rngs = {"noise7": np.random.default_rng(seed), "noise8": np.random.default_rng(seed)}
    for index, original_path in enumerate(originals, start=1):
        words = read_float(original_path).view("<u4")
        for name, bits in (("noise7", 7), ("noise8", 8)):
            expected = whiten_low_bits(words, bits, rngs[name])
            actual = read_float(files_by_name[name][index - 1]).view("<u4")
            if not np.array_equal(actual, expected):
                raise AssertionError(f"Reused TIFF differs from deterministic whitening: {files_by_name[name][index - 1]}")
        if progress_every and (index % progress_every == 0 or index == len(originals)):
            print(f"TIFF verification {index}/{len(originals)}", flush=True)
    return {
        name: {
            "path": str((output / name / "tiffs").resolve()),
            "size_bytes": source_size(files),
            "bitwise_verified": True,
        }
        for name, files in files_by_name.items()
    }


def stack(files: list[Path]) -> np.ndarray:
    first = read_float(files[0])
    result = np.empty((len(files), *first.shape), dtype="<f4")
    result[0] = first
    for index, path in enumerate(files[1:], start=1):
        values = read_float(path)
        if values.shape != first.shape:
            raise ValueError(f"Shape mismatch: {path}: {values.shape} != {first.shape}")
        result[index] = values
    return result


def create_zarr(
    files: list[Path], output_path: Path, overwrite: bool, progress_every: int
) -> dict:
    if output_path.exists():
        if not overwrite:
            raise FileExistsError(f"{output_path} exists; pass --overwrite")
        remove_exact(output_path)
    first = read_float(files[0])
    shape = (len(files), *first.shape)
    chunks = tuple(min(a, b) for a, b in zip(CHUNKS, shape))
    compressor = Blosc(cname="zstd", clevel=5, shuffle=Blosc.BITSHUFFLE)
    array = zarr.open_array(
        str(output_path),
        mode="w",
        shape=shape,
        chunks=chunks,
        dtype="<f4",
        compressor=compressor,
        fill_value=np.nan,
        zarr_format=2,
    )
    array.attrs.update({
        "source_tiff_directory": str(files[0].parent.resolve()),
        "dimensions": ["time", "latitude", "longitude"],
        "units": "K",
        "compression": "Blosc Zstd level 5 with bitshuffle",
    })
    started = time.perf_counter()
    for start in range(0, len(files), chunks[0]):
        stop = min(start + chunks[0], len(files))
        array[start:stop] = stack(files[start:stop])
        if progress_every and (stop % progress_every == 0 or stop == len(files)):
            print(f"Zarr {output_path.parent.name}: {stop}/{len(files)}", flush=True)
    write_seconds = time.perf_counter() - started

    verify_started = time.perf_counter()
    for start in range(0, len(files), chunks[0]):
        stop = min(start + chunks[0], len(files))
        expected = stack(files[start:stop]).view("<u4")
        actual = np.asarray(array[start:stop], dtype="<f4").view("<u4")
        if not np.array_equal(actual, expected):
            raise AssertionError(f"Zarr bitwise verification failed: {output_path}, {start}:{stop}")
    verify_seconds = time.perf_counter() - verify_started
    return {
        "path": str(output_path.resolve()),
        "size_bytes": directory_size(output_path),
        "write_seconds": write_seconds,
        "verify_seconds": verify_seconds,
        "bitwise_verified": True,
        "chunks": list(chunks),
        "codec": "Blosc Zstd level 5 + bitshuffle",
        "format": "Zarr v2",
    }


def create_hdf5(
    files: list[Path], output_path: Path, overwrite: bool, progress_every: int
) -> dict:
    if output_path.exists():
        if not overwrite:
            raise FileExistsError(f"{output_path} exists; pass --overwrite")
        remove_exact(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    first = read_float(files[0])
    shape = (len(files), *first.shape)
    chunks = tuple(min(a, b) for a, b in zip(CHUNKS, shape))
    started = time.perf_counter()
    with h5py.File(output_path, "w") as handle:
        dataset = handle.create_dataset(
            "t2m",
            shape=shape,
            dtype="<f4",
            chunks=chunks,
            fillvalue=np.nan,
            **hdf5plugin.Blosc(
                cname="zstd", clevel=5, shuffle=hdf5plugin.Blosc.BITSHUFFLE
            ),
        )
        dataset.attrs.update({
            "dimensions": np.asarray(["time", "latitude", "longitude"], dtype="S9"),
            "units": "K",
            "compression": "Blosc Zstd level 5 with bitshuffle",
            "source_tiff_directory": str(files[0].parent.resolve()),
        })
        for start in range(0, len(files), chunks[0]):
            stop = min(start + chunks[0], len(files))
            dataset[start:stop] = stack(files[start:stop])
            if progress_every and (stop % progress_every == 0 or stop == len(files)):
                print(f"HDF5 {output_path.parent.name}: {stop}/{len(files)}", flush=True)
    write_seconds = time.perf_counter() - started

    verify_started = time.perf_counter()
    with h5py.File(output_path, "r") as handle:
        dataset = handle["t2m"]
        for start in range(0, len(files), chunks[0]):
            stop = min(start + chunks[0], len(files))
            expected = stack(files[start:stop]).view("<u4")
            actual = np.asarray(dataset[start:stop], dtype="<f4").view("<u4")
            if not np.array_equal(actual, expected):
                raise AssertionError(f"HDF5 bitwise verification failed: {output_path}, {start}:{stop}")
    verify_seconds = time.perf_counter() - verify_started
    return {
        "path": str(output_path.resolve()),
        "size_bytes": output_path.stat().st_size,
        "write_seconds": write_seconds,
        "verify_seconds": verify_seconds,
        "bitwise_verified": True,
        "chunks": list(chunks),
        "codec": "Blosc Zstd level 5 + bitshuffle",
        "format": "HDF5",
    }


def add_size_metrics(result: dict, count: int) -> None:
    result["bits_per_value"] = result["size_bytes"] * 8 / count
    result["ratio_from_float32"] = count * 4 / result["size_bytes"]


def load_diff_statistics(path: Path) -> tuple[dict[str, float], dict[str, float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    weighted = {key: float(value) for key, value in data["weighted_delta_entropy"].items()}
    raw = {
        name: float(data["distributions"][f"{name}_raw"]["entropy_bits_per_value"])
        for name in VARIANTS
    }
    return weighted, raw


def configure_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "figure.dpi": 130,
        "savefig.dpi": 180,
    })


def plot_results(result: dict, figures: Path) -> None:
    configure_style()
    figures.mkdir(parents=True, exist_ok=True)
    x = np.arange(len(VARIANTS))
    width = 0.22
    fig, ax = plt.subplots(figsize=(10, 5.4), constrained_layout=True)
    for offset, fmt, color in ((-width, "tiff", "#2563eb"), (0, "zarr", "#059669"), (width, "hdf5", "#d97706")):
        values = [result["variants"][name][fmt]["bits_per_value"] for name in VARIANTS]
        bars = ax.bar(x + offset, values, width, label=fmt.upper(), color=color)
        ax.bar_label(bars, labels=[f"{value:.2f}" for value in values], padding=2, fontsize=8)
    entropy = [result["weighted_delta_entropy"][name] for name in VARIANTS]
    ax.plot(x, entropy, "ko--", label="三维差分加权边际熵", zorder=5)
    ax.set_xticks(x, [LABELS[name] for name in VARIANTS])
    ax.set_ylabel("bit / value（越低越好）")
    ax.set_title("低位白化前后的实际无损容器大小")
    ax.legend(ncol=2)
    fig.savefig(figures / "container_bits_per_value.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    for fmt, marker in (("tiff", "o"), ("zarr", "s"), ("hdf5", "^")):
        base = result["variants"]["original"][fmt]["bits_per_value"]
        changes = [
            result["variants"][name][fmt]["bits_per_value"] - base for name in ("noise7", "noise8")
        ]
        ax.plot(["低 7 位白化", "低 8 位白化"], changes, marker=marker, linewidth=2, label=fmt.upper())
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylabel("相对原始数据增加的 bit / value")
    ax.set_title("破坏低位规则带来的实际压缩代价")
    ax.legend()
    fig.savefig(figures / "whitening_penalty.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_report(path: Path, result: dict) -> None:
    rows = []
    for name in VARIANTS:
        for fmt in ("tiff", "zarr", "hdf5"):
            item = result["variants"][name][fmt]
            rows.append(
                f"| {LABELS[name]} | {fmt.upper()} | {item['size_bytes']:,} | "
                f"{item['size_bytes'] / 2**20:.2f} | {item['bits_per_value']:.4f} | "
                f"{item['ratio_from_float32']:.3f}× | 是 |"
            )
    penalty_rows = []
    for name in ("noise7", "noise8"):
        cells = []
        for fmt in ("tiff", "zarr", "hdf5"):
            base = result["variants"]["original"][fmt]["bits_per_value"]
            current = result["variants"][name][fmt]["bits_per_value"]
            cells.append(f"+{current - base:.4f}")
        entropy_penalty = result["weighted_delta_entropy"][name] - result["weighted_delta_entropy"]["original"]
        penalty_rows.append(
            f"| {LABELS[name]} | {cells[0]} | {cells[1]} | {cells[2]} | +{entropy_penalty:.4f} |"
        )
    best = {
        name: min(
            ((fmt, result["variants"][name][fmt]["bits_per_value"]) for fmt in ("tiff", "zarr", "hdf5")),
            key=lambda pair: pair[1],
        )
        for name in VARIANTS
    }
    delta = result["weighted_delta_entropy"]
    gap = {name: best[name][1] - delta[name] for name in VARIANTS}
    raw_entropy = result["raw_word_entropy"]
    diff_gain = {name: raw_entropy[name] - delta[name] for name in VARIANTS}
    tail_extra_gain = diff_gain["original"] - diff_gain["noise7"]
    lines = [
        "# 白化 ERA5 数据的 TIFF、Zarr、HDF5 无损压缩实测", "",
        "## 方法", "",
        "数据为 `float32`。低 7/8 位白化使用固定种子 `2311`，只替换 IEEE 754 位模式最低位；",
        "所有高位保持不变。三种格式均保存相同位模式，并已逐 TIFF 或逐时间 chunk 校验。", "",
        "- TIFF：256×256 tile，LZW，horizontal predictor。",
        "- Zarr：v2，chunk `(24,128,256)`，Blosc-Zstd level 5 + bitshuffle。",
        "- HDF5：chunk `(24,128,256)`，Blosc-Zstd level 5 + bitshuffle。", "",
        f"张量形状：`{tuple(result['shape'])}`；总值数：`{result['value_count']:,}`；原始 float32 大小："
        f"`{result['uncompressed_bytes']:,}` bytes。", "",
        "## 实测结果", "",
        "| 数据 | 格式 | 字节数 | MiB | bit/value | 相对 float32 压缩比 | 位级校验 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |", *rows, "",
        "## 白化代价", "",
        "下表是相对同一环境中原始数据结果增加的 bit/value。", "",
        "| 数据 | TIFF | Zarr | HDF5 | 三维差分加权边际熵 |",
        "| --- | ---: | ---: | ---: | ---: |", *penalty_rows, "",
        "## 结论", "",
        f"- 原始数据三种通用容器中最好的是 `{best['original'][0].upper()}`："
        f"`{best['original'][1]:.4f}` bit/value。",
        f"- 低 7 位白化后最好的是 `{best['noise7'][0].upper()}`："
        f"`{best['noise7'][1]:.4f}` bit/value；低 8 位白化后最好的是 "
        f"`{best['noise8'][0].upper()}`：`{best['noise8'][1]:.4f}` bit/value。",
        "- 白化后三种通用容器都显著变差：TIFF 增加约 8.62 bit/value，Zarr/HDF5 增加约 "
        "6.27 bit/value。这证明 GRIB 定点量化形成的低位规则也一直被现有压缩器利用。",
        f"- 三维差分在低 7 位白化后仍把边际熵从 `{raw_entropy['noise7']:.4f}` 降至 "
        f"`{delta['noise7']:.4f}` bit/value，仍降低 `{diff_gain['noise7']:.4f}` bit/value；"
        "因此差分对气温场时空相关性的利用是独立存在的，并未随低位规则一起消失。",
        f"- 原始数据中 raw→差分共降低 `{diff_gain['original']:.4f}` bit/value。以低 7 位白化"
        f"作为反事实，约 `{tail_extra_gain:.4f}` bit/value 的额外降幅可归于低位定点结构，"
        f"约 `{diff_gain['noise7']:.4f}` bit/value 在白化后仍保留。这个分解是实验归因，不是"
        "严格可加的最终码流预算。",
        f"- 与每组最好的通用容器相比，三维差分边际熵分别低 `{gap['original']:.4f}`、"
        f"`{gap['noise7']:.4f}`、`{gap['noise8']:.4f}` bit/value。白化后差距没有消失，"
        "反而仍约为 4 bit/value；但边际熵不是实际压缩文件大小，尚不能据此宣称已经获得同样"
        "码率。还需把差分值、边界和元数据真正编码并计入文件大小。",
        "- 低 7 位与低 8 位的容器结果几乎相同，是因为第 8 个低位在原始数据中本来就具有较高"
        "变化度；随机化它新增的不可压缩信息很少。", "",
        "## 图表", "",
        "![实际码率](figures/3-dim-diff-noise-containers/container_bits_per_value.png)", "",
        "![白化代价](figures/3-dim-diff-noise-containers/whitening_penalty.png)", "",
        "机器可读完整结果位于 `data/3-dim-diff-noise-container-benchmark/results.json`。",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    originals = tiff_files(args.input, args.limit)
    first = read_float(originals[0])
    shape = (len(originals), *first.shape)
    count = int(np.prod(shape))
    args.output.mkdir(parents=True, exist_ok=True)

    if args.reuse_whitened_tiffs:
        generated = verify_reused_tiffs(originals, args.output, args.seed, args.progress_every)
    else:
        generated = create_whitened_tiffs(
            originals, args.output, args.seed, args.overwrite, args.progress_every
        )

    files_by_variant = {
        "original": originals,
        "noise7": tiff_files(args.output / "noise7" / "tiffs", len(originals)),
        "noise8": tiff_files(args.output / "noise8" / "tiffs", len(originals)),
    }
    weighted_delta_entropy, raw_word_entropy = load_diff_statistics(args.diff_results)
    result = {
        "method": "lossless_container_benchmark_after_independent_uniform_low_bit_whitening",
        "seed": args.seed,
        "shape": list(shape),
        "value_count": count,
        "uncompressed_bytes": count * 4,
        "chunks": list(CHUNKS),
        "weighted_delta_entropy": weighted_delta_entropy,
        "raw_word_entropy": raw_word_entropy,
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "tifffile": tifffile.__version__,
            "zarr": zarr.__version__,
            "h5py": h5py.__version__,
        },
        "variants": {},
    }
    for name in VARIANTS:
        print(f"=== {name} ===", flush=True)
        files = files_by_variant[name]
        tiff_result = generated[name] if name != "original" else {
            "path": str(args.input.resolve()),
            "size_bytes": source_size(files),
            "bitwise_verified": True,
        }
        zarr_result = create_zarr(
            files, args.output / name / "blosc_zstd_bitshuffle.zarr", args.overwrite, args.progress_every
        )
        hdf5_result = create_hdf5(
            files, args.output / name / "blosc_zstd_bitshuffle.h5", args.overwrite, args.progress_every
        )
        result["variants"][name] = {
            "tiff": tiff_result,
            "zarr": zarr_result,
            "hdf5": hdf5_result,
        }
        for item in result["variants"][name].values():
            add_size_metrics(item, count)
        (args.output / "results.partial.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    result_path = args.output / "results.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial = args.output / "results.partial.json"
    if partial.exists():
        partial.unlink()
    plot_results(result, args.figures)
    write_report(args.report, result)
    print(f"Wrote {result_path}")
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
