"""Convert ERA5 float32 TIFFs to NetCDF-4 and benchmark lossless filters."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Importing hdf5plugin exposes its filter directory to the HDF5 library used by
# netCDF4.  The module is otherwise intentionally unused.
import hdf5plugin  # noqa: F401
import netCDF4
import numpy as np
import tifffile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_tiffs"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "ERA5-temperature-May2026_netcdf_benchmark"
DEFAULT_REPORT = PROJECT_ROOT / "NetCDFReport.md"
DEFAULT_GRIB = PROJECT_ROOT / "data" / "ERA5-temperature-May2026.grib"
DEFAULT_CHUNKS = (24, 128, 256)


@dataclass(frozen=True)
class Codec:
    name: str
    options: dict[str, Any]
    description: str


@dataclass(frozen=True)
class Result:
    codec: str
    description: str
    output: str
    output_bytes: int
    raw_ratio: float
    tiff_ratio: float
    grib_ratio: float | None
    write_seconds: float
    write_mib_s: float
    read_seconds: float
    read_mib_s: float
    bitwise_identical: bool
    filters: dict[str, Any]


def codecs() -> dict[str, Codec]:
    return {
        "zlib_6_shuffle": Codec(
            "zlib_6_shuffle", {"compression": "zlib", "complevel": 6, "shuffle": True},
            "NetCDF/HDF5 Deflate level 6 with byte shuffle",
        ),
        "zstd_5": Codec(
            "zstd_5", {"compression": "zstd", "complevel": 5},
            "HDF5 Zstandard plugin level 5",
        ),
        "bzip2_6": Codec(
            "bzip2_6", {"compression": "bzip2", "complevel": 6},
            "HDF5 Bzip2 plugin level 6",
        ),
        "blosc_lz4_5_shuffle": Codec(
            "blosc_lz4_5_shuffle", {"compression": "blosc_lz4", "complevel": 5, "shuffle": True},
            "Blosc LZ4 level 5 with internal byte shuffle",
        ),
        "blosc_zstd_5_shuffle": Codec(
            "blosc_zstd_5_shuffle", {"compression": "blosc_zstd", "complevel": 5, "shuffle": True},
            "Blosc Zstandard level 5 with internal byte shuffle",
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--grib", type=Path, default=DEFAULT_GRIB)
    parser.add_argument("--codecs", nargs="+", default=list(codecs()))
    parser.add_argument("--chunks", default=",".join(map(str, DEFAULT_CHUNKS)))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def parse_chunks(text: str) -> tuple[int, int, int]:
    values = tuple(int(value.strip()) for value in text.split(","))
    if len(values) != 3 or any(value < 1 for value in values):
        raise ValueError("--chunks must contain three positive integers")
    return values


def files_in(path: Path, limit: int | None) -> list[Path]:
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")
    files = sorted(item for item in path.glob("*.tif*") if item.is_file())
    files = files[:limit] if limit is not None else files
    if not files:
        raise FileNotFoundError(f"No TIFF files found in {path}")
    return files


def read_tiff(path: Path, shape: tuple[int, int]) -> np.ndarray:
    array = np.asarray(tifffile.imread(path), dtype=np.float32)
    if array.shape != shape:
        raise ValueError(f"{path} has shape {array.shape}, expected {shape}")
    return array


def read_block(files: list[Path], shape: tuple[int, int]) -> np.ndarray:
    block = np.empty((len(files), *shape), dtype=np.float32)
    for index, path in enumerate(files):
        block[index] = read_tiff(path, shape)
    return block


def create_file(
    output: Path, files: list[Path], shape: tuple[int, int], chunks: tuple[int, int, int],
    codec: Codec, input_dir: Path,
) -> tuple[float, dict[str, Any]]:
    time_count = len(files)
    actual_chunks = tuple(min(size, extent) for size, extent in zip(chunks, (time_count, *shape)))
    started = time.perf_counter()
    with netCDF4.Dataset(output, "w", format="NETCDF4") as dataset:
        dataset.title = "ERA5 hourly 2 metre temperature"
        dataset.source = "ERA5 GRIB converted through lossless float32 TIFF"
        dataset.source_tiff_directory = str(input_dir)
        dataset.Conventions = "CF-1.8"
        dataset.createDimension("time", time_count)
        dataset.createDimension("latitude", shape[0])
        dataset.createDimension("longitude", shape[1])

        times = dataset.createVariable("time", "i8", ("time",))
        times.units = "hours since 2026-05-01 00:00:00"
        times.calendar = "proleptic_gregorian"
        times.standard_name = "time"
        times[:] = np.arange(time_count, dtype=np.int64)
        latitude = dataset.createVariable("latitude", "f4", ("latitude",))
        latitude.units = "degrees_north"
        latitude.standard_name = "latitude"
        latitude[:] = np.linspace(90.0, -90.0, shape[0], dtype=np.float32)
        longitude = dataset.createVariable("longitude", "f4", ("longitude",))
        longitude.units = "degrees_east"
        longitude.standard_name = "longitude"
        longitude[:] = np.linspace(0.0, 359.75, shape[1], dtype=np.float32)

        temperature = dataset.createVariable(
            "t2m", "f4", ("time", "latitude", "longitude"),
            chunksizes=actual_chunks, **codec.options,
        )
        temperature.units = "K"
        temperature.standard_name = "air_temperature"
        temperature.long_name = "2 metre temperature"
        temperature.grid_mapping = "latitude_longitude"
        # Write complete time chunks. Writing one frame at a time would force
        # HDF5 to repeatedly update and recompress the same 24-frame chunks.
        time_chunk = actual_chunks[0]
        for start in range(0, time_count, time_chunk):
            stop = min(start + time_chunk, time_count)
            temperature[start:stop] = read_block(files[start:stop], shape)
        filters = temperature.filters()
    return time.perf_counter() - started, filters


def verify_file(output: Path, files: list[Path], shape: tuple[int, int]) -> tuple[float, bool]:
    identical = True
    started = time.perf_counter()
    with netCDF4.Dataset(output) as dataset:
        variable = dataset.variables["t2m"]
        if variable.shape != (len(files), *shape) or variable.dtype != np.dtype("float32"):
            return time.perf_counter() - started, False
        time_chunk = variable.chunking()[0]
        for start in range(0, len(files), time_chunk):
            stop = min(start + time_chunk, len(files))
            decoded = np.asarray(variable[start:stop])
            original = read_block(files[start:stop], shape)
            if not np.array_equal(decoded.view(np.uint32), original.view(np.uint32)):
                identical = False
                break
    return time.perf_counter() - started, identical


def write_report(
    path: Path, frame_count: int, shape: tuple[int, int], chunks: tuple[int, int, int],
    raw_bytes: int, tiff_bytes: int, grib_bytes: int | None, results: list[Result],
) -> None:
    lines = [
        "# ERA5 NetCDF-4 lossless compression report", "",
        "All codecs store the original float32 bit patterns without quantization. Each output was "
        "read back chunk-by-chunk and compared bitwise with its source TIFF.", "",
        f"- Shape: {frame_count} x {shape[0]} x {shape[1]}",
        f"- Chunk shape: {chunks[0]} x {chunks[1]} x {chunks[2]}",
        f"- Raw float32 tensor: {raw_bytes:,} bytes",
        f"- Input TIFF directory: {tiff_bytes:,} bytes",
        f"- Input GRIB: {grib_bytes:,} bytes" if grib_bytes is not None else "- Input GRIB: unavailable for limited run",
        "", "| Codec | NetCDF bytes | Raw ratio | TIFF ratio | GRIB ratio | Write s | Write MiB/s | Verify s | Verify MiB/s | Bitwise |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |",
    ]
    for result in results:
        grib = f"{result.grib_ratio:.3f}x" if result.grib_ratio is not None else "n/a"
        lines.append(
            f"| {result.codec} | {result.output_bytes:,} | {result.raw_ratio:.3f}x | "
            f"{result.tiff_ratio:.3f}x | {grib} | {result.write_seconds:.2f} | "
            f"{result.write_mib_s:.2f} | {result.read_seconds:.2f} | {result.read_mib_s:.2f} | "
            f"{'yes' if result.bitwise_identical else 'NO'} |"
        )
    lines += ["", "`ratio = source bytes / NetCDF bytes`; values above 1 mean the NetCDF is smaller.", "",
              "Verification timing includes reading both the NetCDF output and the source TIFFs; it is not a pure NetCDF read benchmark.", "",
              "Plugin codecs such as Zstandard, Bzip2, and Blosc require the corresponding HDF5 "
              "filter plugin when the file is read. Deflate/zlib has the broadest interoperability.", "",
              "Machine-readable details, including active HDF5 filters, are stored in `results.json`."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    available = codecs()
    unknown = sorted(set(args.codecs) - set(available))
    if unknown:
        raise ValueError(f"Unknown codecs: {', '.join(unknown)}")
    files = files_in(args.input_dir, args.limit)
    chunks = parse_chunks(args.chunks)
    first = np.asarray(tifffile.imread(files[0]), dtype=np.float32)
    if first.ndim != 2:
        raise ValueError(f"Expected 2D TIFF, got {first.shape}")
    shape = tuple(int(value) for value in first.shape)
    raw_bytes = len(files) * first.size * first.dtype.itemsize
    tiff_bytes = sum(path.stat().st_size for path in files)
    grib_bytes = args.grib.stat().st_size if args.grib.is_file() and args.limit is None else None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    for name in args.codecs:
        codec = available[name]
        output = args.output_dir / f"{name}.nc"
        if output.exists() and not args.overwrite:
            raise FileExistsError(f"{output} exists; use --overwrite")
        temporary = output.with_suffix(".nc.tmp")
        if temporary.exists():
            temporary.unlink()
        print(f"Writing {name} -> {output}", flush=True)
        try:
            write_seconds, filters = create_file(temporary, files, shape, chunks, codec, args.input_dir)
            read_seconds, identical = verify_file(temporary, files, shape)
            if not identical:
                raise RuntimeError(f"Bitwise verification failed for {name}")
            temporary.replace(output)
        except BaseException:
            if temporary.exists():
                temporary.unlink()
            raise
        size = output.stat().st_size
        result = Result(
            name, codec.description, output.name, size, raw_bytes / size, tiff_bytes / size,
            grib_bytes / size if grib_bytes is not None else None,
            write_seconds, raw_bytes / (1024**2) / write_seconds,
            read_seconds, raw_bytes / (1024**2) / read_seconds, identical, filters,
        )
        results.append(result)
        print(f"  {size:,} bytes; write {write_seconds:.2f}s; verified={identical}", flush=True)
    write_report(args.report, len(files), shape, chunks, raw_bytes, tiff_bytes, grib_bytes, results)
    metadata = {
        "shape": [len(files), *shape], "chunks": list(chunks), "raw_float32_bytes": raw_bytes,
        "tiff_bytes": tiff_bytes, "grib_bytes": grib_bytes,
        "results": [asdict(result) for result in results],
    }
    (args.output_dir / "results.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.report}")


if __name__ == "__main__":
    main()
