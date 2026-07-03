# Zarr Compression Report

## Input

- TIFF directory: `/home/administrator/GridData/data/ERA5-temperature-May2026_tiffs`
- TIFF file count: `744`
- Array shape: `(744, 721, 1440)` (`time, latitude, longitude`)
- Data type: `float32`
- Uncompressed array bytes: `3089802240` (2.88 GiB)
- Zarr chunks: `(24, 128, 256)`
- Output root: `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark`

## Results

| Codec | Status | Size | Ratio vs Raw | Write Time (s) | Throughput (MiB/s) | Path |
|---|---:|---:|---:|---:|---:|---|
| `blosc_zstd_bitshuffle` | ok | 979.79 MiB | 3.01x | 11.89 | 247.77 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/blosc_zstd_bitshuffle.zarr` |
| `blosc_zstd_shuffle` | ok | 1.01 GiB | 2.84x | 16.74 | 176.04 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/blosc_zstd_shuffle.zarr` |
| `blosc_lz4_bitshuffle` | ok | 1.11 GiB | 2.60x | 12.00 | 245.61 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/blosc_lz4_bitshuffle.zarr` |
| `zlib_6` | ok | 1.49 GiB | 1.93x | 220.72 | 13.35 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/zlib_6.zarr` |
| `gzip_6` | ok | 1.49 GiB | 1.93x | 221.00 | 13.33 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/gzip_6.zarr` |
| `zstd_5` | ok | 1.52 GiB | 1.89x | 36.49 | 80.74 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/zstd_5.zarr` |
| `none` | ok | 3.27 GiB | 0.88x | 7.98 | 369.35 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/none.zarr` |

## Codec Selection Notes

- The benchmark uses Zarr v2 because Zarr 3.1.6 stalled during local array creation in this environment.
- The tested codecs come from numcodecs' documented compression codecs: Blosc, GZip, Zlib, and Zstd.
- Blosc was tested with LZ4 and Zstd backends plus shuffle/bitshuffle variants because these are common choices for numeric arrays.

## Compressor Configs

### `none`

```json
null
```

### `blosc_lz4_bitshuffle`

```json
{
  "blocksize": 0,
  "clevel": 5,
  "cname": "lz4",
  "id": "blosc",
  "shuffle": 2
}
```

### `blosc_zstd_bitshuffle`

```json
{
  "blocksize": 0,
  "clevel": 5,
  "cname": "zstd",
  "id": "blosc",
  "shuffle": 2
}
```

### `blosc_zstd_shuffle`

```json
{
  "blocksize": 0,
  "clevel": 5,
  "cname": "zstd",
  "id": "blosc",
  "shuffle": 1
}
```

### `zstd_5`

```json
{
  "checksum": false,
  "id": "zstd",
  "level": 5
}
```

### `zlib_6`

```json
{
  "id": "zlib",
  "level": 6
}
```

### `gzip_6`

```json
{
  "id": "gzip",
  "level": 6
}
```
