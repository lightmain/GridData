"""Decompress TIFF files under the project data directory.

By default this script reads compressed ``.tif``/``.tiff`` files from
``data/`` and writes uncompressed copies to ``data/decompressed/``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

import tifffile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data"
DEFAULT_OUTPUT_DIR = DEFAULT_INPUT_DIR / "decompressed"
TIFF_EXTENSIONS = {".tif", ".tiff"}

# Tags that are controlled by TiffWriter arguments or by the image data layout.
# Rewriting these via extratags would produce invalid or contradictory files.
MANAGED_TAGS = {
    254,  # NewSubfileType
    256,  # ImageWidth
    257,  # ImageLength
    258,  # BitsPerSample
    259,  # Compression
    262,  # PhotometricInterpretation
    273,  # StripOffsets
    277,  # SamplesPerPixel
    278,  # RowsPerStrip
    279,  # StripByteCounts
    282,  # XResolution
    283,  # YResolution
    284,  # PlanarConfiguration
    296,  # ResolutionUnit
    305,  # Software
    306,  # DateTime
    317,  # Predictor
    322,  # TileWidth
    323,  # TileLength
    324,  # TileOffsets
    325,  # TileByteCounts
    339,  # SampleFormat
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Write uncompressed copies of TIFF files."
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
        help=f"Directory for uncompressed TIFF files. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--suffix",
        default="_decompressed",
        help="Suffix appended before the file extension. Default: _decompressed",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files.",
    )
    return parser.parse_args()


def iter_tiff_files(input_dir: Path, output_dir: Path) -> Iterable[Path]:
    output_dir = output_dir.resolve()
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TIFF_EXTENSIONS:
            continue
        try:
            path.resolve().relative_to(output_dir)
        except ValueError:
            yield path


def copyable_extratags(page: tifffile.TiffPage) -> list[tuple[int, int, int, object, bool]]:
    extratags = []
    for tag in page.tags.values():
        if tag.code in MANAGED_TAGS:
            continue
        try:
            extratags.append((tag.code, tag.dtype.value, tag.count, tag.value, False))
        except Exception as exc:
            print(
                f"Warning: skipped TIFF tag {tag.name} ({tag.code}): {exc}",
                file=sys.stderr,
            )
    return extratags


def output_path_for(source: Path, input_dir: Path, output_dir: Path, suffix: str) -> Path:
    relative = source.relative_to(input_dir)
    return output_dir / relative.with_name(f"{relative.stem}{suffix}{relative.suffix}")


def decompress_tiff(source: Path, target: Path, overwrite: bool = False) -> None:
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} already exists; use --overwrite to replace it")

    target.parent.mkdir(parents=True, exist_ok=True)

    with tifffile.TiffFile(source) as src:
        if len(src.pages) == 0:
            raise ValueError(f"{source} contains no TIFF pages")

        with tifffile.TiffWriter(target, bigtiff=src.is_bigtiff) as dst:
            for index, page in enumerate(src.pages):
                data = page.asarray()
                tile = (page.tilelength, page.tilewidth) if page.is_tiled else None
                dst.write(
                    data,
                    compression=None,
                    photometric=page.photometric,
                    planarconfig=page.planarconfig,
                    extrasamples=page.extrasamples,
                    tile=tile,
                    resolution=page.resolution,
                    resolutionunit=page.resolutionunit,
                    description=page.description if index == 0 else None,
                    metadata=None,
                    extratags=copyable_extratags(page),
                )


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()

    if not input_dir.exists():
        print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
        return 1

    files = list(iter_tiff_files(input_dir, output_dir))
    if not files:
        print(f"No TIFF files found in {input_dir}")
        return 0

    failures = 0
    for source in files:
        target = output_path_for(source, input_dir, output_dir, args.suffix)
        print(f"Decompressing {source} -> {target}")
        try:
            decompress_tiff(source, target, overwrite=args.overwrite)
        except ValueError as exc:
            if "requires the 'imagecodecs' package" in str(exc):
                print(
                    "Error: this TIFF uses a compression codec that requires "
                    "the Python package 'imagecodecs'. Install it in the "
                    "active environment and rerun this script.",
                    file=sys.stderr,
                )
            print(f"Failed: {source}: {exc}", file=sys.stderr)
            failures += 1
        except Exception as exc:
            print(f"Failed: {source}: {exc}", file=sys.stderr)
            failures += 1

    if failures:
        print(f"Completed with {failures} failure(s).", file=sys.stderr)
        return 1

    print(f"Completed. Wrote {len(files)} uncompressed TIFF file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
