# 三个独立三维差分实验计划

## 共用约定

三个项目分别为：

1. `3-dim-diff-cut-tail-grib`
2. `3-dim-diff-cut-tail-float`
3. `3-dim-diff-noise`

每个项目只依赖原始 GRIB/TIFF，不依赖另一个新项目的输出。三者采用相同的
张量轴顺序 `(time, latitude, longitude)`，并把可逆三维前向差分严格划分成
一个三维内部、三个二维面、三条一维棱和一个顶点。统一输出机器可读 JSON、
完整精确频数表、中文报告和 Matplotlib 图表。

统一统计样本数、不同值数量、范围、0 占比、Shannon 边际熵、精确分位数、
最高频值以及 ZigZag 后的最小 bit-width 分布。全量运行使用 744 个小时、
721×1440 空间网格，并使用流式计算避免构造完整 int64 张量。

## 任务一：3-dim-diff-cut-tail-grib

### 目标

从 GRIB `grid_simple` 消息中提取每小时的参考值和二进制数据区打包整数，研究
GRIB 原生整数表示本身的三维差分分布。

### 数据表示

当前文件满足：

\[
T_{t,i}=R_t+X_{t,i}2^{-9},\qquad D_t=0
\]

其中每个 `X` 占 16 bit。因为全部 `R_t` 位于 `[128,256)`，定义精确整数：

\[
r_t=R_t2^{16}
\]

并分别统计一维 `r_t`、一阶时间差分 `Δr_t`、原始三维 `X` 和
`ΔtΔlatΔlon X`。额外统计统一 Q16 格点 `Q=r_t+128X`，用于判断分开建模和
统一物理格点建模的差异。

### 提取与校验

- 用 ecCodes 读取 message 时间、版本、`R/E/D/bitsPerValue` 和扫描顺序。
- 当前文件是 GRIB1，直接解包 Binary Data Section；解析器同时兼容 GRIB2 Section 7。
- 对 16-bit simple packing 直接解包 big-endian `uint16`。
- 与 ecCodes 解码值反推的整数逐元素核对。
- 验证 `r_t` 为整数、整数重建值与 ecCodes 值一致、三维差分完全可逆。

### 产物

- `src/3-dim-diff-cut-tail-grib/analyze.py`
- `data/3-dim-diff-cut-tail-grib/results.json`
- `data/3-dim-diff-cut-tail-grib/histograms/*.csv.gz`
- `reports/ThreeDimDiffCutTailGrib-cn.md`
- `reports/figures/3-dim-diff-cut-tail-grib/*.png`

## 任务二：3-dim-diff-cut-tail-float

### 目标

从每个 float32 时间片提取 256 K 两侧各自恒定的低位，单独形成两个时间序列；
清零这些低位后，再对位模式整数张量做三维差分。

### 变换

- `<256 K`：保存 `tail_below[t] = word mod 128`，清除最低 7 位。
- `>256 K`：保存 `tail_above[t] = word mod 64`，清除最低 6 位。
- `=256 K` 的位模式低位固定为 0，作为可直接识别的特殊值保持不动；不增加第三个序列。

对每小时、每个区域断言尾部只有一个取值，并逐元素验证：

\[
word=(word_{cut}\;|\;tail_t)
\]

两个尾部序列分别统计原始分布和一阶时间差分。对清尾后的 uint32 张量复用统一
三维差分统计，并把内部 stencil 分成全低于 256、全高于 256 和跨界三类，核验
纯低温残差为 128 的倍数、其余残差至少为 64 的倍数。

### 产物

- `src/3-dim-diff-cut-tail-float/analyze.py`
- `data/3-dim-diff-cut-tail-float/results.json`
- `data/3-dim-diff-cut-tail-float/histograms/*.csv.gz`
- `reports/ThreeDimDiffCutTailFloat-cn.md`
- `reports/figures/3-dim-diff-cut-tail-float/*.png`

## 任务三：3-dim-diff-noise

### 目标

破坏原数据最低 7/8 位的规则格点结构，同时严格保持更高位不变，测量三维差分
面对低位独立噪声时的退化程度。

### 噪声定义

分别令 `k=7` 和 `k=8`，使用固定随机种子 `2311`：

\[
word_{noise}=(word\;\&\;\sim(2^k-1))\;|\;U(0,2^k-1)
\]

随机低位在所有时空位置独立。该定义是严格局限在最低 k 位的均匀白化，而不是
可能向高位进位的数值高斯加法。全量断言 `word_noise >> k == word >> k`。

### 分析

分别对原始、7-bit 白化、8-bit 白化位模式执行相同三维差分，比较：

- 原始和差分边际熵；
- 0 残差比例、唯一值数量；
- 64 倍数结构保留比例；
- ZigZag 位宽和理论 bitpacking 码率；
- 噪声引起的实际开尔文扰动范围与 RMSE。

### 产物

- `src/3-dim-diff-noise/analyze.py`
- `data/3-dim-diff-noise/results.json`
- `data/3-dim-diff-noise/histograms/*.csv.gz`
- `reports/ThreeDimDiffNoise-cn.md`
- `reports/figures/3-dim-diff-noise/*.png`

## 完成标准

- 三个项目均能独立全量运行。
- GRIB 整数提取、float 裁尾变换和三维差分均完成相应的精确校验。
- 三份报告使用一致指标和坐标尺度，结论可直接横向比较。
- README 增加三个项目的运行方法和产物说明。
