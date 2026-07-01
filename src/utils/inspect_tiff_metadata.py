"""Inspect TIFF file header and IFD metadata.

The script parses the TIFF structure directly with the Python standard
library. It prints the file header, the number of IFDs in the main chain, and
the tags in each IFD while summarizing tile/strip offset arrays.
"""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIFF = PROJECT_ROOT / "data" / "test_compressed.tiff"

TYPE_INFO = {
    1: ("BYTE", 1, "B"),
    2: ("ASCII", 1, "c"),
    3: ("SHORT", 2, "H"),
    4: ("LONG", 4, "I"),
    5: ("RATIONAL", 8, None),
    6: ("SBYTE", 1, "b"),
    7: ("UNDEFINED", 1, "B"),
    8: ("SSHORT", 2, "h"),
    9: ("SLONG", 4, "i"),
    10: ("SRATIONAL", 8, None),
    11: ("FLOAT", 4, "f"),
    12: ("DOUBLE", 8, "d"),
    13: ("IFD", 4, "I"),
    16: ("LONG8", 8, "Q"),
    17: ("SLONG8", 8, "q"),
    18: ("IFD8", 8, "Q"),
}

TAG_NAMES = {
    254: "NewSubfileType",
    255: "SubfileType",
    256: "ImageWidth",
    257: "ImageLength",
    258: "BitsPerSample",
    259: "Compression",
    262: "PhotometricInterpretation",
    270: "ImageDescription",
    271: "Make",
    272: "Model",
    273: "StripOffsets",
    274: "Orientation",
    277: "SamplesPerPixel",
    278: "RowsPerStrip",
    279: "StripByteCounts",
    282: "XResolution",
    283: "YResolution",
    284: "PlanarConfiguration",
    286: "XPosition",
    287: "YPosition",
    296: "ResolutionUnit",
    305: "Software",
    306: "DateTime",
    315: "Artist",
    317: "Predictor",
    320: "ColorMap",
    322: "TileWidth",
    323: "TileLength",
    324: "TileOffsets",
    325: "TileByteCounts",
    330: "SubIFDs",
    338: "ExtraSamples",
    339: "SampleFormat",
    340: "SMinSampleValue",
    341: "SMaxSampleValue",
    347: "JPEGTables",
    42112: "GDAL_METADATA",
    42113: "GDAL_NODATA",
}

COMPRESSION_NAMES = {
    1: "None",
    2: "CCITT Group 3 1-Dimensional Modified Huffman RLE",
    3: "CCITT Group 3 Fax",
    4: "CCITT Group 4 Fax",
    5: "LZW",
    6: "Old-style JPEG",
    7: "JPEG",
    8: "Deflate / Adobe-style Deflate",
    32773: "PackBits",
    32946: "Deflate",
    34676: "SGILog",
    34677: "SGILog24",
    34712: "JPEG 2000",
    34887: "LERC",
    34925: "LZMA",
    50000: "ZSTD",
    50001: "WebP",
}

PHOTOMETRIC_NAMES = {
    0: "WhiteIsZero",
    1: "BlackIsZero",
    2: "RGB",
    3: "Palette color",
    4: "Transparency mask",
    5: "CMYK",
    6: "YCbCr",
    8: "CIELab",
    32844: "LogL",
    32845: "LogLuv",
}

PLANAR_NAMES = {
    1: "Chunky",
    2: "Planar",
}

SAMPLE_FORMAT_NAMES = {
    1: "Unsigned integer",
    2: "Signed integer",
    3: "IEEE floating point",
    4: "Undefined",
    5: "Complex signed integer",
    6: "Complex IEEE floating point",
}

OFFSET_ARRAY_TAGS = {273, 324}
BYTECOUNT_ARRAY_TAGS = {279, 325}


@dataclass(frozen=True)
class TiffHeader:
    byte_order_marker: bytes
    byte_order_name: str
    endian: str
    magic: int
    is_bigtiff: bool
    offset_size: int
    first_ifd_offset: int


@dataclass(frozen=True)
class IfdEntry:
    tag: int
    dtype: int
    count: int
    value_offset: int
    raw_value: bytes
    total_size: int
    value: object


@dataclass(frozen=True)
class Ifd:
    index: int
    offset: int
    entries: list[IfdEntry]
    next_offset: int


class TiffParseError(RuntimeError):
    """Raised when the TIFF file is malformed or unsupported."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print TIFF header and IFD metadata.")
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=DEFAULT_TIFF,
        help=f"TIFF file to inspect. Default: {DEFAULT_TIFF}",
    )
    parser.add_argument(
        "--max-values",
        type=int,
        default=12,
        help="Maximum scalar values to print for non-offset array tags. Default: 12",
    )
    return parser.parse_args()


def read_exact(handle: BinaryIO, size: int, offset: int | None = None) -> bytes:
    if offset is not None:
        handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        where = f" at offset {offset}" if offset is not None else ""
        raise TiffParseError(f"Expected {size} bytes{where}, got {len(data)} bytes")
    return data


def unpack_one(endian: str, fmt: str, data: bytes) -> int:
    return struct.unpack(endian + fmt, data)[0]


def parse_header(handle: BinaryIO) -> TiffHeader:
    marker = read_exact(handle, 2, 0)
    if marker == b"II":
        endian = "<"
        order_name = "little-endian"
    elif marker == b"MM":
        endian = ">"
        order_name = "big-endian"
    else:
        raise TiffParseError(f"Invalid byte order marker: {marker!r}")

    magic = unpack_one(endian, "H", read_exact(handle, 2))
    if magic == 42:
        first_ifd_offset = unpack_one(endian, "I", read_exact(handle, 4))
        return TiffHeader(marker, order_name, endian, magic, False, 4, first_ifd_offset)

    if magic == 43:
        offset_size = unpack_one(endian, "H", read_exact(handle, 2))
        reserved = unpack_one(endian, "H", read_exact(handle, 2))
        if offset_size != 8 or reserved != 0:
            raise TiffParseError(
                "Unsupported BigTIFF header: "
                f"offset_size={offset_size}, reserved={reserved}"
            )
        first_ifd_offset = unpack_one(endian, "Q", read_exact(handle, 8))
        return TiffHeader(marker, order_name, endian, magic, True, 8, first_ifd_offset)

    raise TiffParseError(f"Unsupported TIFF magic number: {magic}")


def decode_value(endian: str, dtype: int, count: int, value_bytes: bytes) -> object:
    if dtype not in TYPE_INFO:
        return value_bytes

    type_name, type_size, fmt = TYPE_INFO[dtype]
    if type_name == "ASCII":
        return value_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")

    if dtype in {5, 10}:
        numerator_fmt = "i" if dtype == 10 else "I"
        denominator_fmt = "i" if dtype == 10 else "I"
        values = []
        for index in range(count):
            start = index * type_size
            numerator, denominator = struct.unpack(
                endian + numerator_fmt + denominator_fmt,
                value_bytes[start : start + type_size],
            )
            values.append((numerator, denominator))
        return values[0] if count == 1 else values

    if fmt is None:
        return value_bytes

    values = struct.unpack(endian + fmt * count, value_bytes)
    return values[0] if count == 1 else list(values)


def parse_ifd_entry(handle: BinaryIO, header: TiffHeader) -> IfdEntry:
    endian = header.endian
    value_field_size = 8 if header.is_bigtiff else 4

    tag = unpack_one(endian, "H", read_exact(handle, 2))
    dtype = unpack_one(endian, "H", read_exact(handle, 2))
    count_fmt = "Q" if header.is_bigtiff else "I"
    count = unpack_one(endian, count_fmt, read_exact(handle, value_field_size))
    raw_value = read_exact(handle, value_field_size)
    value_offset = unpack_one(endian, "Q" if header.is_bigtiff else "I", raw_value)

    type_size = TYPE_INFO.get(dtype, ("UNKNOWN", 1, None))[1]
    total_size = count * type_size
    if total_size <= value_field_size:
        value_bytes = raw_value[:total_size]
    else:
        next_entry_position = handle.tell()
        value_bytes = read_exact(handle, total_size, value_offset)
        handle.seek(next_entry_position)

    value = decode_value(endian, dtype, count, value_bytes)
    return IfdEntry(tag, dtype, count, value_offset, raw_value, total_size, value)


def parse_ifds(handle: BinaryIO, header: TiffHeader) -> list[Ifd]:
    ifds: list[Ifd] = []
    offset = header.first_ifd_offset
    seen_offsets: set[int] = set()
    count_fmt = "Q" if header.is_bigtiff else "H"
    count_size = 8 if header.is_bigtiff else 2
    next_fmt = "Q" if header.is_bigtiff else "I"
    next_size = 8 if header.is_bigtiff else 4

    while offset:
        if offset in seen_offsets:
            raise TiffParseError(f"IFD chain contains a cycle at offset {offset}")
        seen_offsets.add(offset)

        handle.seek(offset)
        entry_count = unpack_one(header.endian, count_fmt, read_exact(handle, count_size))
        entries = [parse_ifd_entry(handle, header) for _ in range(entry_count)]
        next_offset = unpack_one(header.endian, next_fmt, read_exact(handle, next_size))
        ifds.append(Ifd(len(ifds), offset, entries, next_offset))
        offset = next_offset

    return ifds


def as_sequence(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    return [value]


def first_scalar(value: object) -> object | None:
    values = as_sequence(value)
    return values[0] if values else None


def named_value(value: object, names: dict[int, str]) -> str:
    if isinstance(value, int) and value in names:
        return f"{value} ({names[value]})"
    return str(value)


def summarize_value(entry: IfdEntry, max_values: int) -> str:
    tag_name = TAG_NAMES.get(entry.tag, f"Tag{entry.tag}")
    value = entry.value

    if entry.tag in OFFSET_ARRAY_TAGS:
        return f"{tag_name}: {entry.count} offset value(s), stored at offset {entry.value_offset}"

    if entry.tag in BYTECOUNT_ARRAY_TAGS:
        values = [v for v in as_sequence(value) if isinstance(v, int)]
        if values:
            return (
                f"{tag_name}: {entry.count} byte count value(s), "
                f"total={sum(values)}, min={min(values)}, max={max(values)}"
            )
        return f"{tag_name}: {entry.count} byte count value(s)"

    if entry.tag == 259:
        return f"{tag_name}: {named_value(first_scalar(value), COMPRESSION_NAMES)}"
    if entry.tag == 262:
        return f"{tag_name}: {named_value(first_scalar(value), PHOTOMETRIC_NAMES)}"
    if entry.tag == 284:
        return f"{tag_name}: {named_value(first_scalar(value), PLANAR_NAMES)}"
    if entry.tag == 339:
        values = as_sequence(value)
        named = [named_value(v, SAMPLE_FORMAT_NAMES) for v in values]
        return f"{tag_name}: {named[0] if len(named) == 1 else named}"

    if isinstance(value, bytes):
        if len(value) > max_values:
            return f"{tag_name}: <{len(value)} raw bytes>"
        return f"{tag_name}: {value!r}"

    if isinstance(value, str):
        return f"{tag_name}: {value!r}"

    if isinstance(value, list) and len(value) > max_values:
        return f"{tag_name}: count={len(value)}, first {max_values}={value[:max_values]}"

    return f"{tag_name}: {value}"


def tag_type_name(dtype: int) -> str:
    return TYPE_INFO.get(dtype, ("UNKNOWN", 1, None))[0]


def find_tag(ifd: Ifd, tag: int) -> IfdEntry | None:
    return next((entry for entry in ifd.entries if entry.tag == tag), None)


def print_report(path: Path, header: TiffHeader, ifds: list[Ifd], max_values: int) -> None:
    size = path.stat().st_size
    print(f"File: {path}")
    print(f"File size: {size} bytes")
    print("Header:")
    print(f"  Byte order: {header.byte_order_marker.decode()} ({header.byte_order_name})")
    print(f"  Magic: {header.magic} ({'BigTIFF' if header.is_bigtiff else 'classic TIFF'})")
    print(f"  Offset size: {header.offset_size} bytes")
    print(f"  First IFD offset: {header.first_ifd_offset}")
    print(f"IFD count: {len(ifds)}")

    compression_values: list[int] = []
    for ifd in ifds:
        print()
        print(f"IFD #{ifd.index}:")
        print(f"  Offset: {ifd.offset}")
        print(f"  Entry count: {len(ifd.entries)}")
        print(f"  Next IFD offset: {ifd.next_offset}")

        width = find_tag(ifd, 256)
        height = find_tag(ifd, 257)
        compression = find_tag(ifd, 259)
        tile_offsets = find_tag(ifd, 324)
        strip_offsets = find_tag(ifd, 273)
        layout = "tile" if tile_offsets else "strip" if strip_offsets else "unknown"

        if compression is not None:
            value = first_scalar(compression.value)
            if isinstance(value, int):
                compression_values.append(value)

        print("  Summary:")
        print(f"    Image width: {first_scalar(width.value) if width else 'missing'}")
        print(f"    Image height: {first_scalar(height.value) if height else 'missing'}")
        print(f"    Storage layout: {layout}")
        if compression is not None:
            print(f"    Compression: {named_value(first_scalar(compression.value), COMPRESSION_NAMES)}")
        else:
            print("    Compression: missing")

        print("  Tags:")
        for entry in ifd.entries:
            tag_name = TAG_NAMES.get(entry.tag, f"Tag{entry.tag}")
            print(
                "    "
                f"{entry.tag} ({tag_name}), "
                f"type={tag_type_name(entry.dtype)} ({entry.dtype}), "
                f"count={entry.count}, total_size={entry.total_size}: "
                f"{summarize_value(entry, max_values)}"
            )

    if compression_values:
        names = sorted({COMPRESSION_NAMES.get(value, f"Unknown ({value})") for value in compression_values})
        print()
        print(f"Compression methods in main IFD chain: {', '.join(names)}")


def main() -> int:
    args = parse_args()
    path = args.path.resolve()
    if not path.exists():
        print(f"TIFF file does not exist: {path}")
        return 1

    with path.open("rb") as handle:
        header = parse_header(handle)
        ifds = parse_ifds(handle, header)

    print_report(path, header, ifds, args.max_values)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
