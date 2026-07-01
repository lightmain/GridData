"""Convert GRIB messages to tiled LZW-compressed TIFF files.

By default this script reads the ERA5 GRIB file in ``data/`` and writes one
TIFF per GRIB message to ``data/ERA5-temperature-May2026_tiffs/``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
from eccodes import (
    CodesInternalError,
    codes_get,
    codes_get_values,
    codes_grib_new_from_file,
    codes_release,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRIB = PROJECT_ROOT / "data" / "ERA5-temperature-May2026.grib"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_TILE_SIZE = 256

METADATA_KEYS = (
    "edition",
    "centre",
    "dataDate",
    "dataTime",
    "validityDate",
    "validityTime",
    "stepRange",
    "stepType",
    "shortName",
    "name",
    "paramId",
    "units",
    "typeOfLevel",
    "level",
    "gridType",
    "Ni",
    "Nj",
    "numberOfPoints",
    "iDirectionIncrementInDegrees",
    "jDirectionIncrementInDegrees",
    "latitudeOfFirstGridPointInDegrees",
    "longitudeOfFirstGridPointInDegrees",
    "latitudeOfLastGridPointInDegrees",
    "longitudeOfLastGridPointInDegrees",
    "iScansNegatively",
    "jScansPositively",
    "jPointsAreConsecutive",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert GRIB messages to tiled LZW-compressed TIFF files."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_GRIB,
        help=f"GRIB file to convert. Default: {DEFAULT_GRIB}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for TIFF files. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of GRIB messages to convert.",
    )
    parser.add_argument(
        "--start-index",
        type=int,
        default=1,
        help="1-based GRIB message index to start converting. Default: 1",
    )
    parser.add_argument(
        "--tile-size",
        type=int,
        default=DEFAULT_TILE_SIZE,
        help="Square TIFF tile size in pixels. Default: 256",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing TIFF files.",
    )
    return parser.parse_args()


def get_key(message_id: int, key: str) -> Any:
    try:
        return codes_get(message_id, key)
    except CodesInternalError:
        return None


def read_metadata(message_id: int) -> dict[str, Any]:
    return {key: get_key(message_id, key) for key in METADATA_KEYS}


def grib_datetime(metadata: dict[str, Any], prefix: str) -> datetime | None:
    date_value = metadata.get(f"{prefix}Date")
    time_value = metadata.get(f"{prefix}Time")
    if date_value is None or time_value is None:
        return None

    date_text = str(int(date_value))
    time_text = f"{int(time_value):04d}"
    try:
        return datetime.strptime(date_text + time_text, "%Y%m%d%H%M")
    except ValueError:
        return None


def output_name(message_index: int, metadata: dict[str, Any]) -> str:
    valid_time = grib_datetime(metadata, "validity")
    timestamp = (
        valid_time.strftime("%Y%m%dT%H%M")
        if valid_time is not None
        else f"message_{message_index:04d}"
    )
    short_name = metadata.get("shortName") or "field"
    return f"{message_index:04d}_{short_name}_{timestamp}.tiff"


def normalize_for_json(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    return value


def image_description(message_index: int, metadata: dict[str, Any]) -> str:
    description = {
        "source": "ERA5 GRIB",
        "message_index": message_index,
        "valid_time": (
            grib_datetime(metadata, "validity").isoformat()
            if grib_datetime(metadata, "validity") is not None
            else None
        ),
        "data_time": (
            grib_datetime(metadata, "data").isoformat()
            if grib_datetime(metadata, "data") is not None
            else None
        ),
        "metadata": {key: normalize_for_json(value) for key, value in metadata.items()},
    }
    return json.dumps(description, sort_keys=True, separators=(",", ":"))


def values_to_image(message_id: int, metadata: dict[str, Any]) -> np.ndarray:
    ni = int(metadata["Ni"])
    nj = int(metadata["Nj"])
    values = np.asarray(codes_get_values(message_id), dtype=np.float32)
    expected_size = ni * nj
    if values.size != expected_size:
        raise ValueError(
            f"Expected {expected_size} values from {nj}x{ni} grid, got {values.size}"
        )
    return values.reshape(nj, ni)


def write_tiff(
    target: Path,
    data: np.ndarray,
    metadata: dict[str, Any],
    message_index: int,
    tile_size: int,
    overwrite: bool,
) -> None:
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} already exists; use --overwrite to replace it")

    target.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        target,
        data,
        byteorder="<",
        photometric="minisblack",
        planarconfig="contig",
        compression="lzw",
        predictor=True,
        tile=(tile_size, tile_size),
        description=image_description(message_index, metadata),
        metadata=None,
    )


def convert_grib(
    path: Path,
    output_dir: Path,
    start_index: int,
    limit: int | None,
    tile_size: int,
    overwrite: bool,
) -> int:
    if not path.exists():
        raise FileNotFoundError(f"GRIB file does not exist: {path}")
    if start_index < 1:
        raise ValueError("--start-index must be >= 1")
    if limit is not None and limit < 1:
        raise ValueError("--limit must be >= 1 when provided")
    if tile_size < 16:
        raise ValueError("--tile-size must be >= 16")

    converted = 0
    seen = 0
    started_at = time.monotonic()

    with path.open("rb") as handle:
        while True:
            message_id = codes_grib_new_from_file(handle)
            if message_id is None:
                break

            seen += 1
            try:
                if seen < start_index:
                    continue
                if limit is not None and converted >= limit:
                    break

                metadata = read_metadata(message_id)
                data = values_to_image(message_id, metadata)
                target = output_dir / output_name(seen, metadata)
                write_tiff(target, data, metadata, seen, tile_size, overwrite)
                converted += 1

                elapsed = time.monotonic() - started_at
                print(
                    f"[{converted}] message #{seen} -> {target} "
                    f"shape={data.shape} elapsed={elapsed:.1f}s",
                    flush=True,
                )
            finally:
                codes_release(message_id)

    print(f"Completed. Converted {converted} message(s) from {path} to {output_dir}")
    return converted


def main() -> int:
    args = parse_args()
    try:
        convert_grib(
            path=args.path.resolve(),
            output_dir=args.output_dir.resolve(),
            start_index=args.start_index,
            limit=args.limit,
            tile_size=args.tile_size,
            overwrite=args.overwrite,
        )
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
