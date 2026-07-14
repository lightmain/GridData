# HDF5 压缩报告

## 输入

- TIFF 目录：`/home/administrator/GridData/data/ERA5-temperature-May2026_tiffs`
- TIFF 文件数：`744`
- 数据集：`/temperature_2m`
- 数组形状：`(744, 721, 1440)`（`time, latitude, longitude`）
- 数据类型：`float32`
- 未压缩数组字节数：`3089802240`（2.88 GiB）
- HDF5 分块：`(24, 128, 256)`
- 输出根目录：`/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark`

## 压缩说明

- HDF5 压缩通过每个 dataset 的 filter pipeline 配置。
- 启用压缩的 HDF5 dataset 必须使用 chunked layout；除非显式覆盖，本 benchmark 使用 `(24, 128, 256)`。
- 本报告只测试无损方法：内置 gzip/lzf，以及 hdf5plugin 提供的 LZ4/Blosc/Zstd 过滤器。
- `shuffle` 和 `bitshuffle` 是无损预处理过滤器，用于改善数值数组的压缩效果。
- 内置 gzip level 9 曾单独试跑，但约 30 秒只完成一个 24 小时 chunk，因此跳过完整 benchmark。

## 环境

- h5py：`3.16.0`
- HDF5：`2.1.0`
- hdf5plugin：`6.0.0`
- numpy：`2.4.6`
- tifffile：`2026.3.3`

## 结果

| 编码器 | 状态 | 读回验证 | 大小 | 相对原始数据压缩比 | 写入时间 (s) | 吞吐量 (MiB/s) | 路径 |
|---|---:|---:|---:|---:|---:|---:|---|
| `blosc_zstd_bitshuffle` | ok | True | 979.76 MiB | 3.01x | 23.11 | 127.49 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/blosc_zstd_bitshuffle.h5` |
| `gzip_4_shuffle` | ok | True | 1.02 GiB | 2.82x | 48.72 | 60.48 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/gzip_4_shuffle.h5` |
| `blosc_lz4_bitshuffle` | ok | True | 1.11 GiB | 2.60x | 8.28 | 355.88 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/blosc_lz4_bitshuffle.h5` |
| `lzf_shuffle` | ok | True | 1.33 GiB | 2.16x | 12.27 | 240.19 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/lzf_shuffle.h5` |
| `zstd_5` | ok | True | 1.51 GiB | 1.91x | 34.08 | 86.45 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/zstd_5.h5` |
| `lz4` | ok | True | 2.27 GiB | 1.27x | 12.95 | 227.49 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/lz4.h5` |
| `none` | ok | True | 3.27 GiB | 0.88x | 8.99 | 327.80 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_benchmark/none.h5` |

## 总结

- 最小体积：`blosc_zstd_bitshuffle`，979.76 MiB，相对原始数据 3.01x。
- 压缩输出中写入速度最快：`blosc_lz4_bitshuffle`，1.11 GiB，耗时 8.28s。
- 最好的 HDF5 内置编码器：`gzip_4_shuffle`，1.02 GiB，耗时 48.72s。
- 与之前 Zarr 的最佳结果（`blosc_zstd_bitshuffle`，979.79 MiB，11.89s）相比，HDF5 达到了几乎相同的体积，但本次写入耗时更长。

## 编码器配置

### `none`

无压缩基线

```json
{}
```

### `gzip_4_shuffle`

HDF5 内置 gzip level 4，启用 shuffle

```json
{
  "compression": "gzip",
  "compression_opts": 4,
  "shuffle": true
}
```

### `lzf_shuffle`

h5py LZF，启用 shuffle

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

hdf5plugin Blosc LZ4 level 5，启用 bitshuffle

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

hdf5plugin Blosc Zstandard level 5，启用 bitshuffle

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

## 追加的高等级 Zstd 测试

这次补充测试独立的 `hdf5plugin.Zstd` 高压缩等级。运行时 WSL 可用内存约 12 GiB，而每个 24 小时写入 chunk 约为 95 MiB 的 float32 数据，因此内存不是限制因素。

完整运行前，先用前 24 小时数据估算耗时：

| 编码器 | 24 小时大小 | 24 小时耗时 (s) | 决策 |
|---|---:|---:|---|
| `zstd_9` | 49.13 MiB | 2.16 | 完整运行 |
| `zstd_15` | 49.11 MiB | 7.88 | 跳过；大小几乎与 level 9 相同但慢很多 |
| `zstd_19` | 40.62 MiB | 15.56 | 完整运行 |
| `zstd_22` | 40.60 MiB | 24.13 | 跳过；大小与 level 19 相同但更慢 |

完整运行结果：

| 编码器 | 状态 | 读回验证 | 大小 | 相对原始数据压缩比 | 写入时间 (s) | 吞吐量 (MiB/s) | 路径 |
|---|---:|---:|---:|---:|---:|---:|---|
| `zstd_19` | ok | True | 1.23 GiB | 2.34x | 470.96 | 6.26 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_zstd_high/zstd_19.h5` |
| `zstd_9` | ok | True | 1.49 GiB | 1.93x | 59.24 | 49.74 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_zstd_high/zstd_9.h5` |

与之前的独立 `zstd_5` 结果相比，level 9 只是略小且更慢。level 19 将大小从 1.51 GiB 降到 1.23 GiB，但写入时间从 34.08s 增加到 470.96s。它仍然比 Blosc Zstd bitshuffle 更大且慢得多；在本次 HDF5 测试中，Blosc Zstd bitshuffle 仍然是体积最优方案（979.76 MiB，23.11s）。

## 追加的低等级 Zstd 测试

这次补充测试独立的 `hdf5plugin.Zstd` 低压缩等级 1 到 3。这些等级用于用压缩率换取速度。

完整运行结果：

| 编码器 | 状态 | 读回验证 | 大小 | 相对原始数据压缩比 | 写入时间 (s) | 吞吐量 (MiB/s) | 路径 |
|---|---:|---:|---:|---:|---:|---:|---|
| `zstd_3` | ok | True | 1.55 GiB | 1.86x | 28.13 | 104.75 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_zstd_low/zstd_3.h5` |
| `zstd_2` | ok | True | 1.56 GiB | 1.85x | 18.90 | 155.91 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_zstd_low/zstd_2.h5` |
| `zstd_1` | ok | True | 1.71 GiB | 1.69x | 16.44 | 179.23 | `/home/administrator/GridData/data/ERA5-temperature-May2026_hdf5_zstd_low/zstd_1.h5` |

与之前的独立 `zstd_5` 结果（1.51 GiB，34.08s）相比，level 3 略大但更快，而 level 1 和 2 为了速度牺牲了更多体积。对于这个数据集，低等级独立 Zstd 在体积以及综合体积/速度权衡上都没有超过 Blosc Zstd bitshuffle。
