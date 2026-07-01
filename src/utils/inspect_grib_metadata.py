"""Inspect high-level metadata for a GRIB file.

The script uses ecCodes to walk through GRIB messages without loading the full
data arrays. It reports message counts, unique fields, unique spatial domains,
time coverage, and grid dimensions.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from eccodes import CodesInternalError, codes_get, codes_grib_new_from_file, codes_release


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GRIB = PROJECT_ROOT / "data" / "ERA5-temperature-May2026.grib"


FIELD_KEYS = (
    "shortName",
    "name",
    "paramId",
    "units",
    "typeOfLevel",
    "level",
    "stepType",
)

DOMAIN_KEYS = (
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

MESSAGE_KEYS = (
    "edition",
    "centre",
    "dataDate",
    "dataTime",
    "validityDate",
    "validityTime",
    "stepRange",
    "packingType",
)


@dataclass(frozen=True)
class MessageSummary:
    index: int
    values: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print GRIB metadata summary.")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_GRIB,
        help=f"GRIB file to inspect. Default: {DEFAULT_GRIB}",
    )
    return parser.parse_args()


def get_key(message_id: int, key: str) -> Any:
    try:
        return codes_get(message_id, key)
    except CodesInternalError:
        return None


def read_keys(message_id: int, keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: get_key(message_id, key) for key in keys}


def parse_grib_datetime(date_value: Any, time_value: Any) -> datetime | None:
    if date_value is None or time_value is None:
        return None

    date_text = str(int(date_value))
    time_text = f"{int(time_value):04d}"
    try:
        return datetime.strptime(date_text + time_text, "%Y%m%d%H%M")
    except ValueError:
        return None


def format_datetime(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value is not None else "missing"


def format_value(value: Any) -> str:
    return "missing" if value is None else str(value)


def print_key_values(values: dict[str, Any], indent: str = "  ") -> None:
    for key, value in values.items():
        print(f"{indent}{key}: {format_value(value)}")


def inspect_grib(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"GRIB file does not exist: {path}")

    field_counter: Counter[tuple[tuple[str, Any], ...]] = Counter()
    domain_counter: Counter[tuple[tuple[str, Any], ...]] = Counter()
    packing_counter: Counter[str] = Counter()
    data_times: list[datetime] = []
    valid_times: list[datetime] = []
    first_message: MessageSummary | None = None
    last_message: MessageSummary | None = None
    message_count = 0

    with path.open("rb") as handle:
        while True:
            message_id = codes_grib_new_from_file(handle)
            if message_id is None:
                break

            try:
                message_count += 1
                field_values = read_keys(message_id, FIELD_KEYS)
                domain_values = read_keys(message_id, DOMAIN_KEYS)
                message_values = read_keys(message_id, MESSAGE_KEYS)

                field_counter[tuple(field_values.items())] += 1
                domain_counter[tuple(domain_values.items())] += 1
                packing_counter[str(message_values.get("packingType"))] += 1

                data_time = parse_grib_datetime(
                    message_values.get("dataDate"),
                    message_values.get("dataTime"),
                )
                valid_time = parse_grib_datetime(
                    message_values.get("validityDate"),
                    message_values.get("validityTime"),
                )
                if data_time is not None:
                    data_times.append(data_time)
                if valid_time is not None:
                    valid_times.append(valid_time)

                combined_values = {**message_values, **field_values, **domain_values}
                summary = MessageSummary(message_count, combined_values)
                if first_message is None:
                    first_message = summary
                last_message = summary
            finally:
                codes_release(message_id)

    print(f"File: {path.resolve()}")
    print(f"File size: {path.stat().st_size} bytes")
    print(f"Message count: {message_count}")
    print(f"Unique data fields: {len(field_counter)}")
    print(f"Unique spatial domains: {len(domain_counter)}")

    if valid_times:
        unique_valid_times = sorted(set(valid_times))
        print(f"Unique valid times: {len(unique_valid_times)}")
        print(f"Valid time range: {format_datetime(unique_valid_times[0])} to {format_datetime(unique_valid_times[-1])}")
    if data_times:
        unique_data_times = sorted(set(data_times))
        print(f"Unique data times: {len(unique_data_times)}")
        print(f"Data time range: {format_datetime(unique_data_times[0])} to {format_datetime(unique_data_times[-1])}")

    print()
    print("Fields:")
    for index, (field_items, count) in enumerate(field_counter.most_common(), start=1):
        print(f"  Field #{index}: {count} message(s)")
        print_key_values(dict(field_items), indent="    ")

    print()
    print("Spatial domains:")
    for index, (domain_items, count) in enumerate(domain_counter.most_common(), start=1):
        domain = dict(domain_items)
        ni = domain.get("Ni")
        nj = domain.get("Nj")
        points = domain.get("numberOfPoints")
        print(f"  Domain #{index}: {count} message(s)")
        if ni is not None and nj is not None:
            print(f"    dimensions: latitude={nj}, longitude={ni}")
        if points is not None:
            print(f"    points per message: {points}")
        print_key_values(domain, indent="    ")

    print()
    print("Packing:")
    for packing, count in packing_counter.most_common():
        print(f"  {packing}: {count} message(s)")

    if first_message is not None:
        print()
        print(f"First message #{first_message.index}:")
        print_key_values(first_message.values, indent="  ")

    if last_message is not None and last_message != first_message:
        print()
        print(f"Last message #{last_message.index}:")
        print_key_values(last_message.values, indent="  ")


def main() -> int:
    args = parse_args()
    inspect_grib(args.path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
