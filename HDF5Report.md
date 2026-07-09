# HDF5 Compression Report

## Input

- TIFF directory: `/home/administrator/GridData/data/ERA5-temperature-May2026_tiffs`
- TIFF file count: `744`
- Dataset: `/temperature_2m`
- Array shape: `(744, 721, 1440)` (`time, latitude, longitude`)
- Data type: `float32`
- Uncompressed array bytes: `3089802240` (2.88 GiB)
- HDF5 chunks: `(24, 128, 256)`
- Output root: `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark`

## Compression Notes

- HDF5 compression is configured per dataset through the filter pipeline.
- Compressed HDF5 datasets must use chunked layout; this benchmark uses `(24, 128, 256)` unless overridden.
- This report only benchmarks lossless methods: built-in gzip/lzf and hdf5plugin LZ4/Blosc/Zstd filters.
- `shuffle` and `bitshuffle` are lossless prefilters intended to improve compression of numeric arrays.
- Built-in gzip level 9 was probed separately and skipped from the full benchmark because it completed only one 24-hour chunk in about 30 seconds.

## Environment

- h5py: `3.16.0`
- HDF5: `2.1.0`
- hdf5plugin: `6.0.0`
- numpy: `2.4.6`
- tifffile: `2026.3.3`

## Results

| Codec | Status | Readback | Size | Ratio vs Raw | Write Time (s) | Throughput (MiB/s) | Path |
|---|---:|---:|---:|---:|---:|---:|---|
| `blosc_zstd_bitshuffle` | ok | True | 979.76 MiB | 3.01x | 23.11 | 127.49 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/blosc_zstd_bitshuffle.h5` |
| `gzip_4_shuffle` | ok | True | 1.02 GiB | 2.82x | 48.72 | 60.48 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/gzip_4_shuffle.h5` |
| `blosc_lz4_bitshuffle` | ok | True | 1.11 GiB | 2.60x | 8.28 | 355.88 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/blosc_lz4_bitshuffle.h5` |
| `lzf_shuffle` | ok | True | 1.33 GiB | 2.16x | 12.27 | 240.19 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/lzf_shuffle.h5` |
| `zstd_5` | ok | True | 1.51 GiB | 1.91x | 34.08 | 86.45 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/zstd_5.h5` |
| `lz4` | ok | True | 2.27 GiB | 1.27x | 12.95 | 227.49 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/lz4.h5` |
| `none` | ok | True | 3.27 GiB | 0.88x | 8.99 | 327.80 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/none.h5` |

## Summary

- Best size: `blosc_zstd_bitshuffle`, 979.76 MiB, 3.01x vs raw.
- Best write speed among compressed outputs: `blosc_lz4_bitshuffle`, 1.11 GiB in 8.28s.
- Best built-in HDF5 codec: `gzip_4_shuffle`, 1.02 GiB in 48.72s.
- Compared with the previous Zarr best result (`blosc_zstd_bitshuffle`, 979.79 MiB in 11.89s), HDF5 reached effectively the same size but took longer to write in this run.

## Codec Configs

### `none`

No compression baseline

```json
{}
```

### `gzip_4_shuffle`

Built-in HDF5 gzip level 4 with shuffle

```json
{
  "compression": "gzip",
  "compression_opts": 4,
  "shuffle": true
}
```

### `lzf_shuffle`

h5py LZF with shuffle

```json
{
  "compression": "lzf",
  "shuffle": true
}
```

### `lz4`

hdf5plugin LZ4

```json
{
  "compression": 32004,
  "compression_opts": [
    0
  ]
}
```

### `blosc_lz4_bitshuffle`

hdf5plugin Blosc LZ4 level 5 with bitshuffle

```json
{
  "compression": 32001,
  "compression_opts": [
    0,
    0,
    0,
    0,
    5,
    2,
    1
  ]
}
```

### `blosc_zstd_bitshuffle`

hdf5plugin Blosc Zstandard level 5 with bitshuffle

```json
{
  "compression": 32001,
  "compression_opts": [
    0,
    0,
    0,
    0,
    5,
    2,
    5
  ]
}
```

### `zstd_5`

hdf5plugin Zstandard level 5

```json
{
  "compression": 32015,
  "compression_opts": [
    5
  ]
}
```

## Additional High-Level Zstd Test

This follow-up tests standalone `hdf5plugin.Zstd` at higher compression levels.
Available WSL memory during the run was about 12 GiB, while each 24-hour write
chunk is about 95 MiB of float32 data, so memory was not the limiting factor.

Before the full run, the first 24 hours were tested to estimate runtime:

| Codec | 24-hour Size | 24-hour Time (s) | Decision |
|---|---:|---:|---|
| `zstd_9` | 49.13 MiB | 2.16 | Full run |
| `zstd_15` | 49.11 MiB | 7.88 | Skipped; almost same size as level 9 but much slower |
| `zstd_19` | 40.62 MiB | 15.56 | Full run |
| `zstd_22` | 40.60 MiB | 24.13 | Skipped; same size as level 19 but slower |

Full-run results:

| Codec | Status | Readback | Size | Ratio vs Raw | Write Time (s) | Throughput (MiB/s) | Path |
|---|---:|---:|---:|---:|---:|---:|---|
| `zstd_19` | ok | True | 1.23 GiB | 2.34x | 470.96 | 6.26 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_zstd_high/zstd_19.h5` |
| `zstd_9` | ok | True | 1.49 GiB | 1.93x | 59.24 | 49.74 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_zstd_high/zstd_9.h5` |

Compared with the earlier standalone `zstd_5` result, level 9 is only slightly
smaller and slower. Level 19 reduces size from 1.51 GiB to 1.23 GiB, but write
time increases from 34.08s to 470.96s. It is still larger and much slower than
the Blosc Zstd bitshuffle result, which remains the best HDF5 size result here
(979.76 MiB in 23.11s).
