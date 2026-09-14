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
  ERA5-temperature-May2026_mp4_benchmark/
  ERA5-temperature-May2026_netcdf_benchmark/
  ERA5-temperature-May2026_ffv1_benchmark/
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
| `data/ERA5-temperature-May2026_mp4_benchmark/` | ERA5 温度场的 H.264/H.265 MP4 压缩基准输出。 |
| `data/ERA5-temperature-May2026_netcdf_benchmark/` | 多种无损 NetCDF-4/HDF5 过滤器的完整基准输出。 |
| `data/ERA5-temperature-May2026_ffv1_benchmark/` | 将 float32 原始字节映射到 BGRA 通道的 FFV1/MKV 无损实验。 |
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

### `src/3-dim-diff/uint32_3d_diff_distribution.py`

把每个 `float32` 的 IEEE-754 原始 4 字节无损重解释为 little-endian `uint32`，
然后对 `(time, latitude, longitude)` 张量做可逆的三轴前向差分。脚本流式读取
TIFF，并分别统计三维内部、三个二维边界面、三条一维边界棱和一个顶点的精确
整数频数；差分在 `int64` 中计算以避免无符号回绕。

```bash
conda run -n utils python src/3-dim-diff/uint32_3d_diff_distribution.py \
  --input-dir data/ERA5-temperature-May2026_tiffs \
  --output-dir data/ERA5-temperature-May2026_uint32_3d_diff \
  --report reports/Uint32ThreeDimensionalDiffReport-cn.md \
  --overwrite
```

输出目录中的 `results.json` 是机器可读摘要，`histograms/*.csv.gz` 是各区域完整
的精确 `(value, count)` 频数表。报告同时给出分位数、熵、0 占比、最高频整数和
ZigZag 后所需 bit-width 的分布。

用 Matplotlib 根据上述精确统计生成图表：

```bash
conda run -n utils python src/3-dim-diff/plot_uint32_3d_diff_distribution.py
```

图表默认写入 `reports/figures/uint32_3d_diff/`，并包括区域结构总览、内部差分的
完整频率散点图、ZigZag 位宽累计分布和码率尺度比较。

进一步定位内部差分中少量非 64 倍数的来源：

```bash
conda run -n utils python src/3-dim-diff/analyze_non64_residuals.py
```

该脚本逐个检查 `2 x 2 x 2` 差分 stencil 是否跨越 `float32` 在 256 K 处的指数
边界，输出 `non64_diagnostics.json` 和诊断图，不依赖 ecCodes 或 tifffile。

排除非 64 倍数后，比较内部差分和同均值、同方差的离散化正态分布：

```bash
conda run -n utils python src/3-dim-diff/analyze_multiple64_normality.py
```

输出包括正态性效应量、经验分位数，以及 x 轴为 symlog、y 轴为线性频率的对比图。

### `src/3-dim-diff-cut-tail-grib/analyze.py`

直接解析 GRIB1 Binary Data Section 中的 16-bit simple-packing 整数 `X`，把
`referenceValue` 精确转换为 Q16 整数序列 `r_t`，分别统计 `Δr_t`、
`ΔtΔlatΔlon X` 和统一 Q16 格点的分布。原始码流整数会与 ecCodes 解码结果逐元素核对。

```bash
conda run -n grib python src/3-dim-diff-cut-tail-grib/analyze.py --overwrite
```

结果写入 `data/3-dim-diff-cut-tail-grib/`，报告为
`reports/ThreeDimDiffCutTailGrib-cn.md`。

### `src/3-dim-diff-cut-tail-float/analyze.py`

每个时间片分别保存 `<256 K` 的最低 7 位和 `>256 K` 的最低 6 位，清零尾部后
对 float32 位模式整数进行三维差分，并统计纯低温、纯高温和跨 256 K stencil。
变换在全量运行中执行 bit-exact 恢复校验。

```bash
conda run -n grib python src/3-dim-diff-cut-tail-float/analyze.py --overwrite
```

结果写入 `data/3-dim-diff-cut-tail-float/`，报告为
`reports/ThreeDimDiffCutTailFloat-cn.md`。

### `src/3-dim-diff-noise/analyze.py`

使用固定种子 `2311`，分别把最低 7 位和最低 8 位替换为时空独立均匀随机位，
严格保持更高位不变，再与原始 float32 位模式执行相同三维差分统计。

```bash
conda run -n grib python src/3-dim-diff-noise/analyze.py --overwrite
```

结果写入 `data/3-dim-diff-noise/`，报告为 `reports/ThreeDimDiffNoise-cn.md`。

### `src/3-dim-diff-noise/benchmark_containers.py`

把同一低 7/8 位白化结果实际写成 tiled LZW TIFF、Blosc-Zstd bitshuffle Zarr
和 HDF5，并与原始数据在同一环境中比较文件码率。写入后逐 TIFF 或逐时间 chunk
执行 float32 位级无损校验。

```bash
conda run -n utils python src/3-dim-diff-noise/benchmark_containers.py --overwrite
```

输出位于 `data/3-dim-diff-noise-container-benchmark/`，分析报告为
`reports/WhitenedContainerBenchmark-cn.md`。

三个实验的完整计划见 `plan/three-independent-3d-diff-experiments-cn.md`。

### `src/utils/tiffs_to_mp4_benchmark.py`

将逐小时 `float32` TIFF 序列用全数据集统一温标映射到 8-bit 灰度，再通过
FFmpeg 编码为 MP4。脚本默认比较 H.264 CRF 18/23/28 和 H.265 CRF 28，报告
相对原始 GRIB、TIFF 目录及未压缩 float32 张量的压缩比。每个 MP4 还会被解码
回温度值，以计算 MAE、RMSE、最大误差和 PSNR。该流程是有损压缩，MP4 不可作为
原始科学数据的无损替代品。

完整运行：

```bash
conda run -n utils python src/utils/tiffs_to_mp4_benchmark.py --overwrite
```

快速测试单个配置：

```bash
conda run -n utils python src/utils/tiffs_to_mp4_benchmark.py \
  --limit 10 \
  --profiles h264_crf23 \
  --output-dir data/ERA5-temperature-May2026_mp4_smoke \
  --report Mp4SmokeReport.md \
  --overwrite
```

完整实验结果见 `Mp4Report.md`，机器可读结果位于输出目录的 `results.json`。

### `src/utils/tiffs_to_netcdf_benchmark.py`

将逐小时 `float32` TIFF 序列写为带 CF 坐标和单位的 NetCDF-4 文件，并比较
Deflate、Zstandard、Bzip2、Blosc-LZ4 和 Blosc-Zstandard 五种无损压缩方案。
默认 chunk 为 `(24, 128, 256)`。每个输出都会按 chunk 读回，并和源 TIFF 的
IEEE-754 位模式比较，确保压缩过程没有量化或数值变化。

完整运行：

```bash
conda run -n utils python src/utils/tiffs_to_netcdf_benchmark.py --overwrite
```

只测试兼容性最好的 Deflate 和速度较高的 Blosc-Zstandard：

```bash
conda run -n utils python src/utils/tiffs_to_netcdf_benchmark.py \
  --codecs zlib_6_shuffle blosc_zstd_5_shuffle \
  --overwrite
```

完整结果见 `NetCDFReport.md`。Zstandard、Bzip2 和 Blosc 文件依赖相应 HDF5
filter plugin；需要跨平台和跨软件交换时，优先选择内置 Deflate/zlib 输出。

### `src/utils/tiffs_float32_to_ffv1_benchmark.py`

实验性地把每个 little-endian `float32` 的四个原始字节映射为一个 BGRA 像素：
B、G、R、A 依次保存 bits 0–7、8–15、16–23、24–31，然后用 FFV1 编码到
Matroska。默认比较 Rice/context 0 和 range/context 1 两种 FFV1 配置。每个
输出均完整解码并按 `uint32` 位模式与源 TIFF 比较。

```bash
conda run -n utils python src/utils/tiffs_float32_to_ffv1_benchmark.py --overwrite
```

完整结果见 `FFV1Report.md`。这种编码能够逐位保存数据，但 BGRA 的科学含义是
项目自定义约定，普通视频播放器并不知道像素实际代表四个浮点字节；恢复数据时
必须以 BGRA 解码，并按 little-endian `float32` 重新解释。

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
