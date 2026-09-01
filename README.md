本项目用于研究气象网格数据的本地存储与压缩方案。当前数据集主要是
ERA5 2026 年 5 月逐小时 2 米温度场：744 个时间步，每个时间步是
`721 x 1440` 的 `float32` 全球经纬度网格。

仓库中的代码完成了 GRIB 元数据检查、GRIB 到 TIFF 的转换、TIFF 元数据解析、
TIFF 解压、TIFF 时间序列到 Zarr/HDF5 的压缩基准测试、逐位 Zstd 压缩实验，
以及一个朴素时间差分压缩实验。

## 环境

默认 Python 环境使用 `utils`：

```bash
conda activate utils
```

涉及 GRIB、Zarr、xarray、cfgrib、ecCodes 等气象数据读取与解析时，使用
`grib` 环境：

```bash
conda activate grib
```

如果不想激活环境，也可以使用无状态执行方式：

```bash
conda run -n utils python script.py
conda run -n grib python script.py
```

`requirements.txt` 中列出了 TIFF 工具和简单位平面实验的基础依赖：

```text
tifffile
imagecodecs
zstandard
```

GRIB 和 Zarr 相关脚本还需要 `eccodes`、`zarr`、`numcodecs`、`numpy` 等依赖，
建议直接使用 `grib` 环境运行。

## 数据目录

`data/` 目录被 `.gitignore` 忽略，不会提交到仓库，但项目默认约定如下结构：

```text
data/
  ERA5-temperature-May2026.grib
  ERA5-temperature-May2026_tiffs/
  ERA5-temperature-May2026_tiffs_test/
  ERA5-temperature-May2026-naive_diff/
  ERA5-temperature-May2026_zarr_benchmark/
    none.zarr/
    blosc_lz4_bitshuffle.zarr/
    blosc_zstd_bitshuffle.zarr/
    blosc_zstd_shuffle.zarr/
    zstd_5.zarr/
    zlib_6.zarr/
    gzip_6.zarr/
  ERA5-temperature-May2026_zarr_smoke/
    blosc_zstd_bitshuffle.zarr/
  test_compressed.tiff
```

当前本地数据含义：

| 路径 | 用途 |
| --- | --- |
| `data/ERA5-temperature-May2026.grib` | 原始 ERA5 GRIB 文件，约 1.5 GiB。 |
| `data/ERA5-temperature-May2026_tiffs/` | 由 GRIB 转出的 744 个逐小时 TIFF，文件名形如 `0001_2t_20260501T0000.tiff`。 |
| `data/ERA5-temperature-May2026_tiffs_test/` | 少量 TIFF 测试输出，用于快速验证转换流程。 |
| `data/ERA5-temperature-May2026-naive_diff/` | 朴素时间差分实验输出，仍为 744 个 TIFF。 |
| `data/ERA5-temperature-May2026_zarr_benchmark/` | 多种 Zarr 压缩器的完整基准测试输出。 |
| `data/ERA5-temperature-May2026_zarr_smoke/` | Zarr 小规模 smoke test 输出。 |
| `data/test_compressed.tiff` | TIFF 元数据检查和解压测试用样例。 |

## 程序说明

### `src/utils/inspect_grib_metadata.py`

使用 ecCodes 遍历 GRIB 消息，不加载完整数组，输出消息数量、字段、空间网格、
时间范围和 packing 信息。

推荐显式传入 GRIB 文件路径：

```bash
conda run -n grib python src/utils/inspect_grib_metadata.py \
  data/ERA5-temperature-May2026.grib
```

### `src/utils/grib_to_tiff.py`

把 GRIB 中的每条消息转换为一个 tiled、LZW 压缩的 TIFF。TIFF 数据为
`float32`，并在 `ImageDescription` 中写入消息索引、时间和 GRIB 元数据。

常用命令：

```bash
conda run -n grib python src/utils/grib_to_tiff.py \
  data/ERA5-temperature-May2026.grib \
  --output-dir data/ERA5-temperature-May2026_tiffs \
  --overwrite
```

只转换前 10 条消息用于测试：

```bash
conda run -n grib python src/utils/grib_to_tiff.py \
  data/ERA5-temperature-May2026.grib \
  --output-dir data/ERA5-temperature-May2026_tiffs_test \
  --limit 10 \
  --overwrite
```

重要参数：

| 参数 | 说明 |
| --- | --- |
| `path` | 输入 GRIB 文件，省略时使用脚本内默认路径。 |
| `--output-dir` | TIFF 输出目录。 |
| `--limit` | 最多转换多少条 GRIB 消息。 |
| `--start-index` | 从第几条 GRIB 消息开始，1-based。 |
| `--tile-size` | TIFF 方形 tile 大小，默认 `256`。 |
| `--overwrite` | 覆盖已有输出文件。 |

### `src/utils/tiffs_to_zarr_benchmark.py`

读取一组按文件名排序的 TIFF，把它们写成三维 Zarr 数组：
`time, latitude, longitude`。脚本会测试指定压缩器，记录输出大小、写入耗时、
吞吐量，并生成 Markdown 报告。

运行推荐压缩器：

```bash
conda run -n grib python src/utils/tiffs_to_zarr_benchmark.py \
  --input-dir data/ERA5-temperature-May2026_tiffs \
  --output-root data/ERA5-temperature-May2026_zarr_benchmark \
  --report ZarrReport.md \
  --codecs blosc_zstd_bitshuffle \
  --overwrite
```

运行所有内置压缩器：

```bash
conda run -n grib python src/utils/tiffs_to_zarr_benchmark.py \
  --input-dir data/ERA5-temperature-May2026_tiffs \
  --output-root data/ERA5-temperature-May2026_zarr_benchmark \
  --report ZarrReport.md \
  --codecs all \
  --overwrite
```

内置 codec 名称：

```text
none
blosc_lz4_bitshuffle
blosc_zstd_bitshuffle
blosc_zstd_shuffle
zstd_5
zlib_6
gzip_6
```

默认 chunk 为 `(24, 128, 256)`。当前完整基准测试结果见 `ZarrReport.md`。

### `src/utils/tiffs_to_simplebitshuffle_benchmark.py`

把按文件名排序的 `float32` TIFF 张量按 C-order 拆成 32 个 IEEE-754 位平面。
每个 0/1 位平面先用 `packbits` 收紧为每个值 1 bit，再独立压缩为一个连续的
Zstandard frame。输出不包含 Zarr/HDF5 chunk、坐标或 TIFF 元数据。

完整运行：

```bash
conda run -n utils python src/utils/tiffs_to_simplebitshuffle_benchmark.py \
  --input-dir data/ERA5-temperature-May2026_tiffs \
  --output-dir data/ERA5-temperature-May2026_simplebitshuffle_benchmark \
  --report SimpleBitshuffleReport.md \
  --zstd-level 3 \
  --overwrite
```

先用一张 TIFF 做 smoke test：

```bash
conda run -n utils python src/utils/tiffs_to_simplebitshuffle_benchmark.py \
  --limit 1 \
  --output-dir data/ERA5-temperature-May2026_simplebitshuffle_smoke \
  --report SimpleBitshuffleSmokeReport.md \
  --overwrite
```

输出是 `bit_00_lsb.zst` 至 `bit_31_sign.zst` 共 32 个文件。报告逐位记录
压缩前 packed 大小、压缩后大小和压缩比，并给出最终总大小和端到端吞吐量。

### `src/naive_diff/naive_diff_tiff.py`

把 TIFF 时间序列转换为分块朴素时间差分 TIFF。每个 block 的第一帧作为关键帧
原样复制，后续帧写入：

```text
current_original - previous_original
```

默认 block size 为 `100`。输出仍是 TIFF，差分帧默认使用 `lzma` 压缩。

完整实验命令：

```bash
conda run -n grib python src/naive_diff/naive_diff_tiff.py \
  --input-dir data/ERA5-temperature-May2026_tiffs \
  --output-dir data/ERA5-temperature-May2026-naive_diff \
  --compression lzma \
  --compression-level 9 \
  --overwrite
```

只处理前 100 帧：

```bash
conda run -n grib python src/naive_diff/naive_diff_tiff.py \
  --input-dir data/ERA5-temperature-May2026_tiffs \
  --output-dir data/ERA5-temperature-May2026-naive_diff-test \
  --limit 100 \
  --overwrite
```

当前实验结论见 `NaiveDiffReport.md`：时间差分显著降低了数值范围和方差，但由于
残差仍以 `float32` 存储，最终无损压缩收益较小，完整数据集目录大小约减少
`0.79%`。

### `src/utils/inspect_tiff_metadata.py`

使用 Python 标准库直接解析 TIFF/BigTIFF 文件头和 IFD tag，不依赖 `tifffile`
读取像素数据。适合检查压缩类型、tile/strip 布局、sample format、description
等底层元数据。

示例：

```bash
conda run -n utils python src/utils/inspect_tiff_metadata.py \
  data/test_compressed.tiff
```

限制普通 tag 打印的值数量：

```bash
conda run -n utils python src/utils/inspect_tiff_metadata.py \
  data/ERA5-temperature-May2026_tiffs/0001_2t_20260501T0000.tiff \
  --max-values 20
```

### `src/utils/decompress_tiff.py`

读取压缩 TIFF，写出不压缩的 TIFF 副本，并尽量保留可复制的 TIFF tag。

建议显式指定输入和输出目录：

```bash
conda run -n utils python src/utils/decompress_tiff.py \
  --input-dir data \
  --output-dir data/decompressed \
  --overwrite
```

脚本会递归查找 `.tif` 和 `.tiff` 文件，并避免重新处理输出目录中的文件。
如果遇到需要额外 codec 的 TIFF，请确认当前环境中已安装 `imagecodecs`。

### `src/utils/rename_bin_to_tiff.py`

把目录中的直接子级 `.bin` 文件重命名为 `.tiff`。用于处理内容实际是 TIFF、
但后缀被保存为 `.bin` 的样例数据。

先预览：

```bash
conda run -n utils python src/utils/rename_bin_to_tiff.py \
  data/80MB-tiff \
  --dry-run
```

执行重命名：

```bash
conda run -n utils python src/utils/rename_bin_to_tiff.py \
  data/80MB-tiff
```

如果目标 `.tiff` 已存在，默认会拒绝覆盖；需要覆盖时加 `--overwrite`。

## 推荐工作流

从原始 GRIB 复现实验时，按以下顺序运行：

```bash
# 1. 检查 GRIB 元数据
conda run -n grib python src/utils/inspect_grib_metadata.py \
  data/ERA5-temperature-May2026.grib

# 2. 转为逐时间步 TIFF
conda run -n grib python src/utils/grib_to_tiff.py \
  data/ERA5-temperature-May2026.grib \
  --output-dir data/ERA5-temperature-May2026_tiffs \
  --overwrite

# 3. 跑 Zarr 压缩基准
conda run -n grib python src/utils/tiffs_to_zarr_benchmark.py \
  --input-dir data/ERA5-temperature-May2026_tiffs \
  --output-root data/ERA5-temperature-May2026_zarr_benchmark \
  --report ZarrReport.md \
  --codecs all \
  --overwrite

# 4. 跑朴素时间差分实验
conda run -n grib python src/naive_diff/naive_diff_tiff.py \
  --input-dir data/ERA5-temperature-May2026_tiffs \
  --output-dir data/ERA5-temperature-May2026-naive_diff \
  --compression lzma \
  --compression-level 9 \
  --overwrite
```

## 项目文件

| 文件 | 说明 |
| --- | --- |
| `README.md` | 项目说明、数据目录约定和程序使用方式。 |
| `AGENTS.md` | 本项目给自动化编码助手的本地环境说明。 |
| `requirements.txt` | 基础 TIFF 处理依赖。 |
| `ZarrReport.md` | TIFF 到 Zarr 的压缩基准测试报告。 |
| `NaiveDiffReport.md` | 朴素时间差分压缩实验报告。 |
| `WorkLog.md` | 工作日志，目前为空。 |

## 注意事项

- `data/` 中的原始数据和实验输出体积较大，已被 git 忽略。
- 部分 `src/utils` 脚本的内置默认路径基于脚本所在目录推导，可能不等同于项目根目录下的 `data/`；实际使用时建议像上面的示例一样显式传入输入和输出路径。
- TIFF LZW、LZMA、ZSTD 等压缩读取通常需要 `imagecodecs`。
- Zarr 基准脚本当前按 Zarr v2 API 写入数组；历史报告中记录过本地环境下 Zarr 3.1.6 创建数组卡住的问题。

## 下一步建议

优先验证下面这条路线：

```text
时间差分 → 按允许误差定点量化为整数 → ZigZag/位打包 → ZSTD 或 LZMA
```

建议同时补上：

1. Naive Diff 解码器及逐帧重建校验；
2. 绝对误差、相对误差、RMSE 和极值误差报告；
3. 压缩率、压缩/解压吞吐量、随机访问代价三类 benchmark；
4. `float32 + byte shuffle/bitshuffle`、FPZIP/FPC、ZFP、SZ 等基线；
5. 不同块大小、变量和空间分块方式的对比实验。

对于允许有限精度损失的气象数据，定点量化能把“残差数值很小”真正转化为“每个残差需要的有效位更少”，比继续更换通用 TIFF codec 更有希望获得数量级更明显的压缩收益。
