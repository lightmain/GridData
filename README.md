# GridData

一个用于研究**气象格点数据压缩方法及其工程实现**的小型实验项目。目前项目以 2026 年 5 月 ERA5 全球逐小时 2 米气温数据为样本，主要验证了：

1. 如何把 GRIB 中的每个时次转换成便于独立访问的浮点 TIFF；
2. 气象场的时间连续性是否能通过帧间差分转化为压缩收益；
3. 通用 TIFF 压缩算法对 `float32` 原始场和差分场的实际效果。

当前最重要的实验结论是：**时间差分显著降低了数据的数值范围和方差，但直接把差分保存为 `float32` 并不能充分降低字节熵，因此无损压缩收益很小。** 下一步应把时间差分与定点量化、整数位打包或浮点专用压缩结合起来。

## 项目做了什么

项目当前的数据处理链路如下：

```text
ERA5 GRIB
  │ 逐条读取 GRIB message，保留气象元数据
  ▼
每时次一个 float32 TIFF（256×256 分块，LZW + 浮点 Predictor）
  │ 按时间排序，每 100 帧组成一个独立块
  ▼
块首帧原样保存 + 后续帧保存 current - previous（LZMA TIFF）
  │
  └─ 统计原始场与差分场的范围、均值、方差和最终目录体积
```

差分编码以 100 帧为一组：

```text
D[0] = X[0]                       # key frame，原 TIFF 直接复制
D[t] = X[t] - X[t-1], t = 1..99  # diff frame，float32 TIFF
```

解码时从块首帧开始逐帧累加：

```text
X[0] = D[0]
X[t] = X[t-1] + D[t]
```

这种分块方式限制了随机读取和错误传播的范围，但块内非关键帧必须从该块首帧开始顺序重建。当前仓库实现了编码，**尚未提供 Naive Diff 的重建脚本**；`decompress_tiff.py` 只负责移除 TIFF 编解码器压缩，并不重建时间差分。

## 已完成实验

样本是 ERA5 2026 年 5 月逐小时 2 米气温，共 744 个时次。完整实验结果记录在 [NaiveDiffReport.md](NaiveDiffReport.md)。

### 数据统计

| 指标 | 原始温度场 | 差分场（不含关键帧） |
| --- | ---: | ---: |
| 样本数 | 772,450,560 | 764,144,640 |
| 最小值 | 197.8103 K | -14.2518 K |
| 最大值 | 324.1006 K | 15.0403 K |
| 均值 | 280.0083 K | 0.00262 K |
| 方差 | 447.9063 | 0.48305 |

差分后的动态范围和方差都大幅下降，说明 ERA5 温度场具有很强的逐小时连续性，时间预测本身是有效的。

### 压缩结果

前 100 帧的 TIFF 编解码器对比：

| 存储方式 | 体积 |
| --- | ---: |
| 原始 LZW TIFF | 161,601,423 B |
| Naive Diff + LZW | 208,771,055 B |
| Naive Diff + ZSTD level 9 | 170,245,454 B |
| Naive Diff + Deflate level 9 | 173,880,917 B |
| Naive Diff + LZMA | 159,687,708 B |

LZMA 是已测试方法中效果最好的，因此完整实验采用 LZMA 保存差分帧：

| 数据目录 | 文件数 | 体积 |
| --- | ---: | ---: |
| 原始 LZW TIFF | 744 | 1,202,308,785 B |
| Naive Diff TIFF | 744（8 个关键帧 + 736 个差分帧） | 1,192,804,567 B |
| 减少 | — | 9,504,218 B（约 0.79%） |

收益较小的根本原因不是差分预测无效，而是表示方式没有改变：差分值仍占用 32 位 IEEE-754 浮点数，低位尾数还可能因减法变得更像噪声。LZW、Deflate、ZSTD 和 LZMA 等通用压缩器看到的是字节模式，并不会因为一个浮点数的绝对值较小就自动使用更少的位。

## 目录结构

```text
GridData/
├─ data/                              # 本地实验数据，已被 git 忽略
├─ src/
│  ├─ naive_diff/
│  │  └─ naive_diff_tiff.py           # 分块时间差分编码与流式统计
│  └─ utils/
│     ├─ inspect_grib_metadata.py      # 用 ecCodes 汇总 GRIB 元数据
│     ├─ grib_to_tiff.py               # GRIB message → float32 TIFF
│     ├─ inspect_tiff_metadata.py      # 仅用标准库解析 TIFF/BigTIFF IFD
│     ├─ decompress_tiff.py            # 输出不使用 TIFF 压缩的副本
│     └─ rename_bin_to_tiff.py         # 将实际为 TIFF 的 .bin 文件改后缀
├─ NaiveDiffReport.md                  # Naive Diff 完整实验记录
├─ requirements.txt                    # TIFF 相关 Python 依赖
└─ WorkLog.md                          # 预留工作日志（当前为空）
```

本地 `data/` 当前还保留了原始 GRIB、744 个原始 TIFF、744 个差分 TIFF，以及一组名为 `80MB-tiff` 的额外 TIFF 样本。这些文件体积较大，不纳入版本控制。

## 环境准备

项目约定普通 Python 任务使用 `utils` Conda 环境，GRIB 读取使用包含 ecCodes/cfgrib 的 `grib` 环境。

```powershell
# 普通 TIFF 工具和 Naive Diff
conda activate utils
pip install -r requirements.txt

# GRIB 检查与转换
conda activate grib
conda install xarray cfgrib eccodes dask netcdf4 matplotlib numpy pandas jupyterlab
conda install zarr numcodecs
pip install -r requirements.txt
```

也可以采用无状态调用：

```powershell
conda run -n utils python <script> [参数...]
conda run -n grib python <script> [参数...]
```

`requirements.txt` 目前只列出了 `tifffile` 和 `imagecodecs`。运行 GRIB 脚本还需要 `numpy` 和 `eccodes`，运行差分脚本需要 `numpy`；推荐直接使用上述 Conda 环境。

## 快速开始

以下命令均在仓库根目录执行。

### 1. 查看 GRIB 内容

该工具逐条读取 message 的高层元数据，不会把全部格点数组同时载入内存。它会报告变量、空间范围、时间范围、网格尺寸和 GRIB packing 类型。

```powershell
conda run -n grib python src/utils/inspect_grib_metadata.py `
  data/ERA5-temperature-May2026.grib
```

### 2. 将 GRIB 转为 TIFF 序列

每个 GRIB message 写成一个小端 `float32`、单波段、256×256 tiled、LZW 压缩的 TIFF。文件名包含 message 序号、变量短名和有效时间，GRIB 元数据则以 JSON 写入 `ImageDescription`。

```powershell
conda run -n grib python src/utils/grib_to_tiff.py `
  data/ERA5-temperature-May2026.grib `
  --output-dir data/ERA5-temperature-May2026_tiffs
```

先做少量试跑：

```powershell
conda run -n grib python src/utils/grib_to_tiff.py `
  data/ERA5-temperature-May2026.grib `
  --output-dir data/tiff-smoke-test `
  --limit 3
```

常用参数还有 `--start-index`、`--tile-size` 和 `--overwrite`。

### 3. 查看 TIFF 底层结构

检查器直接解析 classic TIFF/BigTIFF 的 header、IFD 和 tag，无需 `tifffile`：

```powershell
conda run -n utils python src/utils/inspect_tiff_metadata.py `
  data/ERA5-temperature-May2026_tiffs/0001_2t_20260501T0000.tiff
```

### 4. 运行 Naive Diff 实验

```powershell
conda run -n utils python src/naive_diff/naive_diff_tiff.py `
  --input-dir data/ERA5-temperature-May2026_tiffs `
  --output-dir data/ERA5-temperature-May2026-naive_diff `
  --block-size 100 `
  --compression lzma `
  --compression-level 9 `
  --overwrite
```

脚本按文件名字典序处理 TIFF，因此输入文件名必须保持可排序的时间/序号前缀。它一次只保留当前帧和上一帧，但统计方差时会为当前整帧临时转成 `float64`。

试跑前 10 帧可增加：

```text
--limit 10
```

执行结束会输出 JSON 摘要，包括关键帧数、差分帧数和两类数据的流式统计结果。

### 5. 其他 TIFF 工具

把目录中的压缩 TIFF 改写成无压缩 TIFF（注意这会显著增加磁盘占用）：

```powershell
conda run -n utils python src/utils/decompress_tiff.py `
  --input-dir data/ERA5-temperature-May2026_tiffs `
  --output-dir data/decompressed
```

预览将 `.bin` 后缀改成 `.tiff` 的操作：

```powershell
conda run -n utils python src/utils/rename_bin_to_tiff.py `
  data/80MB-tiff `
  --dry-run
```

确认无误后移除 `--dry-run` 即可执行重命名。

## 当前限制

- Naive Diff 只有编码器，没有对应的序列重建和逐值一致性验证工具。
- 差分仍保存为 `float32`，没有量化、定点整数、bitpacking、shuffle/bitshuffle 或误差界控制。
- 实验只覆盖一个变量（ERA5 `2t`）和一个月，尚未比较降水、风场、气压等不同统计特性的变量。
- 文件名决定处理顺序，脚本不会从 TIFF 元数据中重新排序或验证时间连续性。
- 差分 TIFF 的描述只保存来源文件、前一文件和块位置，没有完整继承原 TIFF 的地理/气象元数据。
- `inspect_grib_metadata.py`、`inspect_tiff_metadata.py`、`decompress_tiff.py` 和 `rename_bin_to_tiff.py` 当前计算出的默认数据路径位于 `src/data`。在修正源码前，请像本文示例一样显式传入仓库根目录下的 `data/...` 路径。
- 当前没有自动化测试、统一的实验配置文件或机器可读的 benchmark 结果文件。

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
