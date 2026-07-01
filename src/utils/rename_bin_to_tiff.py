"""Rename .bin files in data/80MB-tiff to .tiff.

The files are TIFF files with a nonstandard .bin suffix. This script renames
only direct children of the target directory and does not overwrite existing
files unless --overwrite is provided.
"""

from __future__ import annotations

import argparse
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_DIR = PROJECT_ROOT / "data" / "80MB-tiff"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rename .bin files to .tiff.")
    parser.add_argument(
        "target_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_TARGET_DIR,
        help=f"Directory containing .bin files. Default: {DEFAULT_TARGET_DIR}",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .tiff files with the same stem.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned renames without changing files.",
    )
    return parser.parse_args()


def rename_bin_files(target_dir: Path, overwrite: bool, dry_run: bool) -> int:
    target_dir = target_dir.resolve()
    if not target_dir.exists():
        raise FileNotFoundError(f"Target directory does not exist: {target_dir}")
    if not target_dir.is_dir():
        raise NotADirectoryError(f"Target path is not a directory: {target_dir}")

    bin_files = sorted(path for path in target_dir.iterdir() if path.is_file() and path.suffix.lower() == ".bin")
    if not bin_files:
        print(f"No .bin files found in {target_dir}")
        return 0

    for source in bin_files:
        target = source.with_suffix(".tiff")
        if target.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing file: {target}")

    for source in bin_files:
        target = source.with_suffix(".tiff")
        print(f"{source.name} -> {target.name}")
        if not dry_run:
            if overwrite and target.exists():
                target.unlink()
            source.rename(target)

    action = "Would rename" if dry_run else "Renamed"
    print(f"{action} {len(bin_files)} file(s) in {target_dir}")
    return len(bin_files)


def main() -> int:
    args = parse_args()
    rename_bin_files(args.target_dir, overwrite=args.overwrite, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
