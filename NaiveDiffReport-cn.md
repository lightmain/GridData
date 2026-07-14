# Naive Diff 实验报告

## 背景

本项目研究气象网格数据的压缩方法。本实验使用的基线存储方式是：每个时间戳对应一个 tile 存储、LZW 压缩的 TIFF 文件。

测试的 Naive Diff 方法按每 100 个文件为一组存储时间序列：

- 每组中的第一个 TIFF 保持不变，作为关键帧。
- 同一组中后续每个 TIFF 存储：

```text
current_original - previous_original
```

对于 2026 年 5 月 ERA5 2 米气温数据集，总共有 744 个文件，被分成 8 组。前 7 组每组包含 100 个文件，最后一组包含 44 个文件。

## 为什么压缩收益很小

时间差分显著降低了数值范围和方差：

- 原始温度值范围约为 197.81 K 到 324.10 K。
- 差分值范围约为 -14.25 K 到 15.04 K。
- 原始方差约为 447.91。
- 差分方差约为 0.48。

但是，最终存储大小只略有改善。主要原因是该实现将差分仍然存为 `float32` 值。通用无损字节压缩器处理的是二进制表示，而不是语义上的数值范围。较小的浮点值并不一定拥有紧凑或低熵的字节表示：

- IEEE-754 `float32` 每个值始终使用 32 bit。
- 接近的十进制数仍然可能在许多尾数 bit 上不同。
- 两个 `float32` 场相减可能产生低位尾数 bit 噪声较多的残差。
- LZW、Deflate、ZSTD、LZMA 等 TIFF 压缩器可以利用重复字节模式，但它们不会自动知道一个小的浮点残差本可以用更少的数值 bit 表示。

因此，Naive Diff 变换在统计上是有用的，但直接把残差存为 `float32` 会保留大部分逐值 bit 宽度。

## 更适合小浮点残差的压缩方法

对于浮点气象场，常见选项包括：

1. 将残差量化为整数，然后使用整数压缩。

   如果允许精度容差，可以存储：

   ```text
   int_residual = round((current - previous) / scale)
   ```

   `scale` 控制绝对精度。量化后，bitpacking、delta coding、entropy coding、ZSTD 或 LZMA 都能更直接地利用较小的整数范围。

2. 差分前先以定点整数存储温度。

   例如，以 centi-Kelvin 或 milli-Kelvin 为单位将温度存为整数，然后对整数场做差分。这本质上是受控量化，并使 bitpacking 可用。

3. 使用面向浮点的无损压缩器。

   FPZIP 或 FPC 等算法会预测浮点值，并编码 IEEE-754 bit pattern 的 XOR 或残差结构。它们比通用字节压缩器更适合利用平滑科学浮点数组。

4. 使用带误差边界的有损科学压缩器。

   ZFP 和 SZ 等算法可以强制绝对、相对或精度误差边界。当不要求 bitwise 精确重建时，它们通常对网格科学数据更有效。

5. 在通用压缩前进行 shuffle 或 bitshuffle。

   Byte shuffle 或 bitshuffle 会重排数值，使符号位、指数、尾数字节或 bit 聚集在一起。这可以让 ZSTD、LZ4、Deflate 等编码器更有效地压缩浮点数组。

本项目最有希望的下一步可能是：

```text
temporal diff -> controlled quantization/fixed-point integer -> bitpacking or ZSTD
```

这能直接利用观测到的小残差范围。

## 实现

Naive Diff 实现在：

```text
src/naive_diff/naive_diff_tiff.py
```

脚本按时间戳顺序读取 TIFF 文件，为每个输入 TIFF 写出一个输出 TIFF，并报告原始值和残差值的流式统计信息。

完整实验使用的最终命令是：

```bash
conda run -n grib python src/naive_diff/naive_diff_tiff.py \
  --output-dir data/ERA5-temperature-May2026-naive_diff \
  --compression lzma \
  --compression-level 9 \
  --overwrite
```

输出文件写入：

```text
data/ERA5-temperature-May2026-naive_diff
```

数据文件通过 `.gitignore` 被 git 忽略。

## 压缩选择

在前 100 帧上测试了几种 TIFF 编码器：

| 编码器 | 前 100 帧大小 |
| --- | ---: |
| 原始 LZW TIFF | 161,601,423 bytes |
| Naive Diff + LZW | 208,771,055 bytes |
| Naive Diff + ZSTD level 9 | 170,245,454 bytes |
| Naive Diff + Deflate level 9 | 173,880,917 bytes |
| Naive Diff + LZMA | 159,687,708 bytes |

LZMA 是测试过的 TIFF 编码器中表现最好的，因此最终输出对差分帧使用 TIFF LZMA 压缩。TIFF 元数据检查显示：

```text
Compression: 34925 (LZMA)
Predictor: 3
TileWidth: 256
TileLength: 256
SampleFormat: IEEE floating point
```

## 完整数据集结果

输入：

```text
data/ERA5-temperature-May2026_tiffs
```

输出：

```text
data/ERA5-temperature-May2026-naive_diff
```

文件数量：

| 指标 | 数值 |
| --- | ---: |
| TIFF 文件总数 | 744 |
| 原样复制的关键帧 | 8 |
| 差分帧 | 736 |
| 分组大小 | 100 |

存储大小：

| 数据集 | 大小 |
| --- | ---: |
| 原始 TIFF 目录 | 1,202,308,785 bytes |
| Naive Diff 目录 | 1,192,804,567 bytes |
| 减少量 | 9,504,218 bytes |
| 减少比例 | about 0.79% |

全部 744 个原始 TIFF 的原始值统计：

| 统计量 | 数值 |
| --- | ---: |
| Count | 772,450,560 |
| Min | 197.810302734375 |
| Max | 324.1005859375 |
| Mean | 280.0082946833511 |
| Variance | 447.9062597971049 |

差分值统计，不包含 8 个关键帧：

| 统计量 | 数值 |
| --- | ---: |
| Count | 764,144,640 |
| Min | -14.251800537109375 |
| Max | 15.040283203125 |
| Mean | 0.0026180964021852667 |
| Variance | 0.4830509629461122 |

## 结论

Naive Diff 证明 ERA5 2 米气温场具有很强的时间连续性。残差相比原始值拥有小得多的范围和方差。

但是，当前残差仍然以 `float32` TIFF 图像存储。这意味着每个残差仍保留 32 bit 浮点表示，通用 TIFF 压缩器只能利用字节模式冗余。因此最终无损存储减少很小：约 0.79%。

该实验表明，时间差分是一个有用的预测器，但它应当与真正能降低残差 bit 宽度的表示方式结合。下一步实验应将残差按显式精度 scale 转换为定点整数，然后在这些整数残差上测试 bitpacking 和 ZSTD/LZMA。
