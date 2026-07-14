# Zarr 压缩报告

## 输入

- TIFF 目录：`/home/administrator/GridData/data/ERA5-temperature-May2026_tiffs`
- TIFF 文件数：`744`
- 数组形状：`(744, 721, 1440)`（`time, latitude, longitude`）
- 数据类型：`float32`
- 未压缩数组字节数：`3089802240`（2.88 GiB）
- Zarr 分块：`(24, 128, 256)`
- 输出根目录：`/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark`

## 结果

| 编码器 | 状态 | 大小 | 相对原始数据压缩比 | 写入时间 (s) | 吞吐量 (MiB/s) | 路径 |
|---|---:|---:|---:|---:|---:|---|
| `blosc_zstd_bitshuffle` | ok | 979.79 MiB | 3.01x | 11.89 | 247.77 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/blosc_zstd_bitshuffle.zarr` |
| `blosc_zstd_shuffle` | ok | 1.01 GiB | 2.84x | 16.74 | 176.04 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/blosc_zstd_shuffle.zarr` |
| `blosc_lz4_bitshuffle` | ok | 1.11 GiB | 2.60x | 12.00 | 245.61 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/blosc_lz4_bitshuffle.zarr` |
| `zlib_6` | ok | 1.49 GiB | 1.93x | 220.72 | 13.35 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/zlib_6.zarr` |
| `gzip_6` | ok | 1.49 GiB | 1.93x | 221.00 | 13.33 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/gzip_6.zarr` |
| `zstd_5` | ok | 1.52 GiB | 1.89x | 36.49 | 80.74 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/zstd_5.zarr` |
| `none` | ok | 3.27 GiB | 0.88x | 7.98 | 369.35 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_benchmark/none.zarr` |

## 编码器选择说明

- 本 benchmark 使用 Zarr v2，因为 Zarr 3.1.6 在该环境中创建本地数组时发生卡顿。
- 测试的编码器来自 numcodecs 文档中的压缩编码器：Blosc、GZip、Zlib 和 Zstd。
- Blosc 测试了 LZ4 和 Zstd 后端，并结合 shuffle/bitshuffle 变体，因为这些是数值数组常用选择。

## 压缩器配置

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

## 追加的高等级 Zstd 测试

这次补充测试独立的 `numcodecs.Zstd` 高压缩等级。运行时 WSL 可用内存约 12 GiB，而每个 24 小时写入 chunk 约为 95 MiB 的 float32 数据，因此内存不是限制因素。

完整运行前，先用前 24 小时数据估算耗时：

| 编码器 | 24 小时大小 | 24 小时耗时 (s) | 决策 |
|---|---:|---:|---|
| `zstd_9` | 49.51 MiB | 2.16 | 完整运行 |
| `zstd_15` | 49.48 MiB | 8.49 | 跳过；大小几乎与 level 9 相同但慢很多 |
| `zstd_19` | 40.60 MiB | 15.62 | 完整运行 |
| `zstd_22` | 40.59 MiB | 24.41 | 跳过；大小与 level 19 相同但更慢 |

完整运行结果：

| 编码器 | 状态 | 大小 | 相对原始数据压缩比 | 写入时间 (s) | 吞吐量 (MiB/s) | 路径 |
|---|---:|---:|---:|---:|---:|---|
| `zstd_19` | ok | 1.23 GiB | 2.34x | 466.90 | 6.31 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_zstd_high/zstd_19.zarr` |
| `zstd_9` | ok | 1.50 GiB | 1.91x | 55.79 | 52.81 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_zstd_high/zstd_9.zarr` |

与之前的独立 `zstd_5` 结果相比，level 9 只是略小且更慢。level 19 将大小从 1.52 GiB 降到 1.23 GiB，但写入时间从 36.49s 增加到 466.90s。它仍然比 Blosc Zstd bitshuffle 更大且慢得多；在本次 Zarr 测试中，Blosc Zstd bitshuffle 仍然是最佳选择（979.79 MiB，11.89s）。

## 追加的低等级 Zstd 测试

这次补充测试独立的 `numcodecs.Zstd` 低压缩等级 1 到 3。这些等级用于用压缩率换取速度。

完整运行结果：

| 编码器 | 状态 | 大小 | 相对原始数据压缩比 | 写入时间 (s) | 吞吐量 (MiB/s) | 路径 |
|---|---:|---:|---:|---:|---:|---|
| `zstd_3` | ok | 1.56 GiB | 1.85x | 26.13 | 112.75 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_zstd_low/zstd_3.zarr` |
| `zstd_2` | ok | 1.57 GiB | 1.84x | 18.93 | 155.69 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_zstd_low/zstd_2.zarr` |
| `zstd_1` | ok | 1.72 GiB | 1.67x | 16.31 | 180.71 | `/home/administrator/GridData/data/ERA5-temperature-May2026_zarr_zstd_low/zstd_1.zarr` |

与之前的独立 `zstd_5` 结果（1.52 GiB，36.49s）相比，level 3 略大但更快，而 level 1 和 2 为了速度牺牲了更多体积。对于这个数据集，低等级独立 Zstd 在体积以及综合体积/速度权衡上都没有超过 Blosc Zstd bitshuffle。
