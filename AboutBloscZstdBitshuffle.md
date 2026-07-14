# About Blosc Zstd Bitshuffle

本文以当前项目中的 ERA5 2 米气温数据为例，介绍 Blosc Zstd bitshuffle 的执行流程。

Blosc 指一种面向多维数值数组的无损压缩框架。无损压缩表示：压缩后再解压，得到的数据和压缩前完全一致。

## 1. Blosc 要处理什么数据

在当前项目里，Blosc 处理的是 ERA5 2 米气温数据。数据类型是 `float32`，也就是 32 位浮点数。每个数占 4 字节。

主数组形状是：

```text
(744, 721, 1440)
```

这三个维度分别表示：

```text
time, latitude, longitude
```

也就是：

```text
744 个小时
721 个纬度点
1440 个经度点
```

在 Zarr 或 HDF5 里，我们没有把整个大数组一次性压缩，而是先把数组切成固定大小的 chunk。这里统一使用 `chunk` 这个词，表示外层存储格式交给压缩器的一块数据。

当前 chunk 大小是：

```text
(24, 128, 256)
```

一个 chunk 包含：

```text
24 * 128 * 256 = 786,432 个 float32
```

一个 `float32` 占 4 字节，所以一个 chunk 的原始大小是：

```text
786,432 * 4 = 3,145,728 字节
约 3 MiB
```

Blosc 每次接收的主要输入就是这样一个约 3 MiB 的 chunk。

## 2. Blosc 的总体流程

Blosc 对一个 chunk 的处理流程可以按顺序理解为：

1. 接收一个 chunk。
2. 把这个 chunk 再切成更小的 block。
3. 对每个 block 做 bitshuffle。
4. 用 Zstd 压缩每个经过 bitshuffle 的 block。
5. 把所有压缩后的 block 和必要的描述信息写成一个 Blosc 压缩结果。

这里需要定义几个词。

`block` 表示 Blosc 在内部使用的更小数据块。`chunk` 是 Zarr 或 HDF5 交给 Blosc 的数据块，`block` 是 Blosc 为了更高效执行而继续切出来的数据块。后面只用 `chunk` 表示外层数据块，只用 `block` 表示 Blosc 内部数据块。

`bitshuffle` 表示一种无损重排方法。它不改变数值，只改变 bit 的排列顺序。bit 是二进制位，一个 bit 只能是 0 或 1。

`Zstd` 表示 Zstandard 压缩算法。Zstd 是真正执行压缩的算法之一。Blosc 可以调用不同的压缩算法；在 `Blosc Zstd bitshuffle` 里，Blosc 调用的是 Zstd。

## 3. 为什么 Blosc 要把 chunk 切成 block

一个 chunk 约 3 MiB。Blosc 会把 chunk 切成多个 block，原因主要有两个。

第一个原因是并行。多个 block 可以由多个 CPU 线程同时处理。线程表示 CPU 上可以独立执行的一条执行路径。假设一个 chunk 被切成 8 个 block，那么 Blosc 可以让多个线程分别处理这些 block。这样总耗时会低于逐个处理所有 block。

第二个原因是缓存。CPU cache 是 CPU 附近的小容量高速存储。CPU 读取 CPU cache 里的数据比读取主内存里的数据快。较小的 block 更容易被 CPU cache 容纳。这样处理 block 时，CPU 等待内存读取的时间会减少。

在当前项目里，一个 chunk 约 3 MiB。Blosc 内部的 block 通常比 chunk 小。具体 block 大小由 Blosc 根据数据类型、压缩器和运行环境选择。我们不需要依赖一个固定数值来理解流程，只需要知道：Blosc 继续切分 chunk，是为了并行处理和改善 CPU cache 访问。

## 4. bitshuffle 在 block 里做什么

当前数据是 `float32`。每个 `float32` 有 32 个 bit。

假设一个 block 里有很多 `float32` 数值：

```text
v0, v1, v2, v3, ...
```

每个值都有 32 个 bit。普通存储顺序是：

```text
v0 的 32 个 bit
v1 的 32 个 bit
v2 的 32 个 bit
v3 的 32 个 bit
...
```

bitshuffle 会改成：

```text
所有值的第 31 位 bit
所有值的第 30 位 bit
所有值的第 29 位 bit
...
所有值的第 0 位 bit
```

这个操作只重排 bit，不修改 bit。解压时，Blosc 会做相反的重排，把 bit 放回原来的位置。

为什么这样有用？当前数据是 2 米气温，单位是 Kelvin。大多数值大约在 200 K 到 315 K 之间。相邻经纬度点的气温通常接近，相邻小时的气温通常也接近。因此，一个 block 里的很多 `float32` 数值在高位 bit 上相似。

bitshuffle 把相同位置的 bit 放到一起。相似的高位 bit 聚在一起后，会形成更容易被压缩的二进制序列。Zstd 处理这样的序列时，通常能得到更小的结果。

举一个简化例子。为了方便说明，假设每个值只有 8 个 bit，实际 `float32` 是 32 个 bit。

原始值是：

```text
v0 = 10110010
v1 = 10110011
v2 = 10110100
v3 = 10110101
```

普通排列是：

```text
10110010 10110011 10110100 10110101
```

bitshuffle 后，把每个值同一位置的 bit 放在一起：

```text
第 7 位：1 1 1 1
第 6 位：0 0 0 0
第 5 位：1 1 1 1
第 4 位：1 1 1 1
第 3 位：0 0 0 0
第 2 位：0 0 1 1
第 1 位：1 1 0 0
第 0 位：0 1 0 1
```

重排后的序列包含更多连续的相同 bit，例如 `1111` 和 `0000`。Zstd 更容易压缩这种序列。

这个例子只用于说明 bit 位置重排。实际数据中，一个 block 里有大量 `float32`，因此这种相似性更明显。

## 5. Zstd 在 Blosc 里做什么

Zstd 是 Blosc 调用的压缩算法。Zstd 接收 bitshuffle 之后的 block，并输出压缩后的 block。

Zstd 会寻找输入字节序列中的重复模式和统计规律，然后用更短的表示保存这些模式。这里不展开 Zstd 的内部算法，只需要把 Zstd 理解为一个通用无损压缩算法。

在当前组合里，Zstd 的输入已经被 bitshuffle 处理过。bitshuffle 让浮点数组的相似性更容易体现在字节序列中，所以 Zstd 的压缩效果更好。

## 6. Blosc 如何组织并行执行

Blosc 会对多个 block 并行执行相同流程：

```text
block -> bitshuffle -> Zstd
```

如果 CPU 有多个可用核心，Blosc 可以让多个线程同时处理不同 block。

例如，一个 chunk 被切成多个 block。线程 1 处理第 1 个 block，线程 2 处理第 2 个 block，线程 3 处理第 3 个 block。每个线程都执行 bitshuffle 和 Zstd。所有线程完成后，Blosc 把压缩结果组合起来。

这个并行过程是 Blosc 提高速度的重要原因。

## 7. Blosc 压缩结果里需要保存什么

Blosc 压缩一个 chunk 后，需要保存足够的信息，保证之后能够解压。

压缩结果通常包含：

1. 原始数据大小。
2. 数据类型元素大小，例如当前是 4 字节。
3. 使用了哪个预处理方法，例如 bitshuffle。
4. 使用了哪个压缩算法，例如 Zstd。
5. 每个压缩后 block 的位置和大小。
6. 每个压缩后 block 的数据。

这些信息让解压流程可以准确恢复原始 chunk。

这里统一把这些信息称为“描述信息”。描述信息不是气温数据本身，但解压需要它。

## 8. 解压流程

解压时，Blosc 按相反顺序执行：

1. 读取 Blosc 压缩结果中的描述信息。
2. 找到每个压缩后的 block。
3. 用 Zstd 解压每个 block。
4. 对每个 block 做 bitshuffle 的逆操作。
5. 把所有 block 按原顺序拼回 chunk。

如果一切正常，得到的 chunk 和压缩前完全一致。

## 9. 为什么当前数据上 Blosc Zstd bitshuffle 表现最好

当前结果中，Zarr 的最佳结果是：

```text
Blosc Zstd bitshuffle: 979.79 MiB, 11.89s
```

HDF5 的最佳体积结果是：

```text
Blosc Zstd bitshuffle: 979.76 MiB, 23.11s
```

它表现好，原因可以分成三层。

第一层，bitshuffle 适合 `float32` 气温网格。气温场在空间和时间上连续，相邻值相似。bitshuffle 把这种相似性转成更容易压缩的 bit 序列。

第二层，Zstd 压缩率较好。Zstd 比 LZ4 这类更偏速度的压缩算法通常能得到更小文件。

第三层，Blosc 让执行过程更快。Blosc 把 chunk 切成 block，使用 CPU cache 更友好的 block，并用多个线程处理 block。

所以这个组合同时利用了：

```text
bitshuffle 的数据重排
Zstd 的压缩能力
Blosc 的分块和并行执行
```

这三点正好适合当前 ERA5 `float32` 气温数组。

## 10. 为什么单独提高 Zstd 等级不如这个组合

我们也测试了独立 Zstd 的多个等级。

Zarr 中：

```text
zstd_5:  1.52 GiB, 36.49s
zstd_19: 1.23 GiB, 466.90s
```

HDF5 中：

```text
zstd_5:  1.51 GiB, 34.08s
zstd_19: 1.23 GiB, 470.96s
```

独立 Zstd level 19 比 level 5 更小，但耗时显著增加。即使 level 19，体积仍大于 Blosc Zstd bitshuffle 的约 980 MiB。

原因是独立 Zstd 直接压缩原始 `float32` 字节序列。原始字节序列没有经过 bitshuffle，因此 Zstd 看到的重复模式不如 bitshuffle 后明显。

提高 Zstd 等级表示 Zstd 花更多时间寻找更好的压缩表示。bitshuffle 则是在 Zstd 压缩前改变输入排列，让输入本身更容易压缩。对于当前浮点网格数据，后者更有效。

## 11. 为什么 Blosc LZ4 bitshuffle 更快但更大

另一个结果是：

```text
Blosc LZ4 bitshuffle: 1.11 GiB, 8.28s  （HDF5）
Blosc LZ4 bitshuffle: 1.11 GiB, 12.00s （Zarr）
```

这里的 LZ4 是另一种无损压缩算法。LZ4 的特点是速度快，压缩率通常低于 Zstd。

这个结果说明：

```text
bitshuffle + Blosc 的执行框架仍然有效
LZ4 后端让速度更快
Zstd 后端让体积更小
```

如果最关心写入速度，Blosc LZ4 bitshuffle 很有竞争力。如果最关心存储体积，Blosc Zstd bitshuffle 更好。

## 12. 当前例子的完整执行流程

以一个 Zarr chunk 为例，当前流程是：

1. Zarr 取出形状为 `(24, 128, 256)` 的 `float32` chunk。
2. Zarr 把这个 chunk 交给 Blosc。
3. Blosc 把 chunk 切成多个 block。
4. Blosc 对每个 block 做 bitshuffle。
5. Blosc 用多个线程调用 Zstd 压缩不同 block。
6. Blosc 把压缩后的 block 和描述信息组合成压缩结果。
7. Zarr 把压缩结果写入磁盘。

HDF5 使用 hdf5plugin 调用 Blosc 时，核心压缩流程相同。区别在于，外层存储格式从 Zarr 换成 HDF5，写入文件和记录 dataset 信息的方式不同。

## 13. 最后总结

Blosc 的速度来自几个具体机制：

1. 把 chunk 切成更小的 block。
2. 用多个线程并行处理 block。
3. 让 block 大小更适合 CPU cache。
4. 对固定类型数组使用 bitshuffle 这种快速无损预处理。
5. 把预处理和压缩整合成一个连续流程，减少额外开销。

Blosc Zstd bitshuffle 在当前数据上表现最好，是因为当前数据正好满足这些条件：

```text
数据是规则的 float32 数组
相邻时间和相邻空间的值相似
bitshuffle 能暴露这种相似性
Zstd 能有效压缩 bitshuffle 后的数据
Blosc 能并行、高效地执行整个流程
```

所以它不是因为某一个步骤单独“非常强”，而是因为这些步骤组合后适合当前数据。
