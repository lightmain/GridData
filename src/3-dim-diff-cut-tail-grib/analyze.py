"""Analyse GRIB simple-packing integers and their reversible 3-D deltas."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import eccodes as ec
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from three_dim_diff_common import (  # noqa: E402
    ExactDistribution,
    StreamedThreeDimensionalDelta,
    save_bundle,
    weighted_delta_entropy,
)

DEFAULT_INPUT = PROJECT_ROOT / "data" / "ERA5-temperature-May2026.grib"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "3-dim-diff-cut-tail-grib"
DEFAULT_REPORT = PROJECT_ROOT / "reports" / "ThreeDimDiffCutTailGrib-cn.md"
DEFAULT_FIGURES = PROJECT_ROOT / "reports" / "figures" / "3-dim-diff-cut-tail-grib"


def packed_data_payload(message: bytes) -> bytes:
    """Return only the packed grid-point bits from a GRIB1/2 message."""
    if message[:4] != b"GRIB":
        raise ValueError("Missing GRIB marker")
    edition = message[7]
    if edition == 1:
        offset = 8
        pds_length = int.from_bytes(message[offset : offset + 3], "big")
        flags = message[offset + 7]
        offset += pds_length
        if flags & 0x80:  # Grid Description Section present.
            offset += int.from_bytes(message[offset : offset + 3], "big")
        if flags & 0x40:  # Bit-map Section present.
            offset += int.from_bytes(message[offset : offset + 3], "big")
        bds_length = int.from_bytes(message[offset : offset + 3], "big")
        # The low nibble records padding bits; this file uses one full pad byte.
        return message[offset + 11 : offset + bds_length]
    if edition == 2:
        offset = 16
        while offset < len(message) - 4:
            length = int.from_bytes(message[offset : offset + 4], "big")
            number = message[offset + 4]
            if length < 5:
                raise ValueError(f"Invalid section length {length}")
            if number == 7:
                return message[offset + 5 : offset + length]
            offset += length
        raise ValueError("GRIB2 Section 7 not found")
    raise ValueError(f"Unsupported GRIB edition {edition}")


def raw_simple_packing_integers(gid: int, shape: tuple[int, int]) -> np.ndarray:
    packing = ec.codes_get(gid, "packingType")
    bits = int(ec.codes_get(gid, "bitsPerValue"))
    bitmap = int(ec.codes_get(gid, "bitmapPresent"))
    if packing != "grid_simple" or bits != 16 or bitmap:
        raise ValueError(
            f"Expected grid_simple/16-bit/no bitmap, got {packing}/{bits}/{bitmap}"
        )
    payload = packed_data_payload(ec.codes_get_message(gid))
    count = math.prod(shape)
    if len(payload) < count * 2:
        raise ValueError(f"Packed payload is {len(payload)} bytes, expected at least {count * 2}")
    return np.frombuffer(payload[: count * 2], dtype=">u2", count=count).astype(np.int64).reshape(shape)


def configure_style() -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    plt.rcParams.update({
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "figure.dpi": 130,
        "savefig.dpi": 180,
    })


def plot_results(result: dict, output: Path) -> None:
    configure_style()
    output.mkdir(parents=True, exist_ok=True)
    d = result["distributions"]
    names = ["x_raw", "x_interior", "q16_raw", "q16_interior", "reference_q16_delta"]
    labels = ["X 原值", "X 三维差分", "Q16 原值", "Q16 三维差分", "ΔR(Q16)"]
    entropy = [d[n]["entropy_bits_per_value"] for n in names]
    unique = [d[n]["unique_count"] for n in names]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), constrained_layout=True)
    axes[0].bar(labels, entropy, color=["#64748b", "#2563eb", "#94a3b8", "#0f766e", "#d97706"])
    axes[0].set_ylabel("bit / value")
    axes[0].set_title("原始整数与差分的边际熵")
    axes[0].tick_params(axis="x", rotation=18)
    axes[0].bar_label(axes[0].containers[0], fmt="%.2f")
    axes[1].bar(labels, unique, color=["#64748b", "#2563eb", "#94a3b8", "#0f766e", "#d97706"])
    axes[1].set_yscale("log")
    axes[1].set_ylabel("unique values")
    axes[1].set_title("不同整数数量")
    axes[1].tick_params(axis="x", rotation=18)
    axes[1].bar_label(axes[1].containers[0], labels=[f"{x:,}" for x in unique], fontsize=8)
    fig.suptitle("GRIB 原生整数三维差分")
    fig.savefig(output / "entropy_and_cardinality.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)

    top = d["x_interior"]["top_values"][:21]
    values = [x["value"] for x in top]
    fractions = [x["fraction"] for x in top]
    fig, ax = plt.subplots(figsize=(11, 5), constrained_layout=True)
    ax.bar(range(len(values)), fractions, color="#2563eb")
    ax.set_xticks(range(len(values)), [str(x) for x in values], rotation=55, ha="right")
    ax.set_ylabel("frequency")
    ax.set_title("X 的三维内部差分：最高频整数")
    fig.savefig(output / "x_interior_top_values.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def write_report(path: Path, result: dict) -> None:
    d = result["distributions"]
    rows = []
    for name, label in (
        ("reference_q16", "r_t = R_t·2^16"),
        ("reference_q16_delta", "Δr_t"),
        ("x_raw", "X 原始整数"),
        ("x_interior", "X 三维内部差分"),
        ("q16_raw", "Q=r+128X 原值"),
        ("q16_interior", "Q 三维内部差分"),
    ):
        s = d[name]
        rows.append(
            f"| {label} | {s['count']:,} | {s['unique_count']:,} | {s['min']:,} | "
            f"{s['max']:,} | {s['zero_fraction']:.6%} | {s['entropy_bits_per_value']:.4f} |"
        )
    lines = [
        "# ERA5 GRIB 原生整数与三维差分统计",
        "",
        "## 方法与校验",
        "",
        "逐 message 读取 GRIB1 Binary Data Section，并按 big-endian uint16 直接解包 `X`。",
        "当前 744 个 message 均为 `grid_simple`、16 bit、无 bitmap，且 `E=-9,D=0`。",
        "参考值全部位于 `[128,256)`，所以 `r=R·2^16` 是精确整数；统一 Q16 格点为",
        "`Q=r+128X`。BDS 原始整数已与 ecCodes 解码值逐元素核对。",
        "",
        f"形状：`{tuple(result['shape'])}`；处理耗时 `{result['elapsed_seconds']:.2f}` 秒。",
        f"最大重建误差：`{result['validation']['maximum_decode_error']}` K。",
        "",
        "## 核心统计",
        "",
        "| 对象 | 数量 | 不同值 | 最小值 | 最大值 | 0 占比 | 熵 (bit/value) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        *rows,
        "",
        "## 观察",
        "",
        f"- `r_t` 有 `{d['reference_q16']['unique_count']}` 个不同值；一阶差分熵为 "
        f"`{d['reference_q16_delta']['entropy_bits_per_value']:.4f} bit/value`。",
        f"- X 原始边际熵为 `{d['x_raw']['entropy_bits_per_value']:.4f}`，内部三维差分为 "
        f"`{d['x_interior']['entropy_bits_per_value']:.4f} bit/value`。",
        f"- X 完整差分张量的分区加权边际熵为 `{result['x_weighted_delta_entropy']:.4f} bit/value`。",
        f"- 统一 Q16 格点内部差分熵为 `{d['q16_interior']['entropy_bits_per_value']:.4f}`；"
        "该基线把每小时变化的参考值重新并入场值。",
        "- 边际熵不包含上下文模型和实际码流开销，不能直接等同于最终压缩率。",
        "",
        "## 输出",
        "",
        "`results.json` 保存所有摘要；`histograms/*.csv.gz` 保存完整精确频数。",
        "",
        "## 图表",
        "",
        "![GRIB 整数熵与取值数](figures/3-dim-diff-cut-tail-grib/entropy_and_cardinality.png)",
        "",
        "![X 内部差分最高频值](figures/3-dim-diff-cut-tail-grib/x_interior_top_values.png)",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--figures", type=Path, default=DEFAULT_FIGURES)
    p.add_argument("--limit", type=int)
    p.add_argument("--progress-every", type=int, default=25)
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if (args.output / "results.json").exists() and not args.overwrite:
        raise FileExistsError("results.json exists; pass --overwrite")
    x_analysis = StreamedThreeDimensionalDelta()
    q_analysis = StreamedThreeDimensionalDelta()
    references = ExactDistribution()
    reference_deltas = ExactDistribution()
    times: list[str] = []
    reference_q16_values: list[int] = []
    previous_r: int | None = None
    max_error = 0.0
    started = time.perf_counter()
    count = 0
    with args.input.open("rb") as stream:
        while True:
            gid = ec.codes_grib_new_from_file(stream)
            if gid is None or (args.limit is not None and count >= args.limit):
                if gid is not None:
                    ec.codes_release(gid)
                break
            try:
                ni, nj = int(ec.codes_get(gid, "Ni")), int(ec.codes_get(gid, "Nj"))
                shape = (nj, ni)
                reference = float(ec.codes_get(gid, "referenceValue"))
                exponent = int(ec.codes_get(gid, "binaryScaleFactor"))
                decimal = int(ec.codes_get(gid, "decimalScaleFactor"))
                if exponent != -9 or decimal != 0:
                    raise ValueError(f"Unexpected scales E={exponent}, D={decimal}")
                r = int(round(math.ldexp(reference, 16)))
                if math.ldexp(r, -16) != reference:
                    raise AssertionError(f"Reference is not exact Q16: {reference}")
                x = raw_simple_packing_integers(gid, shape)
                decoded = np.asarray(ec.codes_get_values(gid)).reshape(shape)
                reconstructed = reference + np.ldexp(x.astype(np.float64), exponent)
                error = float(np.max(np.abs(decoded - reconstructed)))
                max_error = max(max_error, error)
                if error != 0:
                    raise AssertionError(f"Raw X reconstruction error {error}")
                x_analysis.update(x)
                q_analysis.update(r + 128 * x)
                references.update([r])
                if previous_r is not None:
                    reference_deltas.update([r - previous_r])
                previous_r = r
                date = int(ec.codes_get(gid, "validityDate"))
                clock = int(ec.codes_get(gid, "validityTime"))
                times.append(f"{date:08d}T{clock:04d}")
                reference_q16_values.append(r)
                count += 1
            finally:
                ec.codes_release(gid)
            if args.progress_every and count % args.progress_every == 0:
                print(f"Processed {count} GRIB messages", flush=True)
    x_dist = x_analysis.distributions()
    q_dist = q_analysis.distributions()
    distributions = {f"x_{k}": v for k, v in x_dist.items()}
    distributions.update({f"q16_{k}": v for k, v in q_dist.items()})
    distributions["reference_q16"] = references
    distributions["reference_q16_delta"] = reference_deltas
    metadata = {
        "method": "direct_grib1_bds_uint16_and_reference_q16",
        "input": str(args.input.resolve()),
        "shape": list(x_analysis.shape),
        "times": times,
        "reference_q16_values": reference_q16_values,
        "packing": {"packingType": "grid_simple", "bitsPerValue": 16, "E": -9, "D": 0},
        "validation": {"maximum_decode_error": max_error},
        "elapsed_seconds": time.perf_counter() - started,
    }
    result = save_bundle(args.output, metadata, distributions)
    x_view = {"distributions": {k: result["distributions"][f"x_{k}"] for k in x_dist}}
    result["x_weighted_delta_entropy"] = weighted_delta_entropy(x_view)
    (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_report(args.report, result)
    plot_results(result, args.figures)
    print(f"Wrote {args.output / 'results.json'}")
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
