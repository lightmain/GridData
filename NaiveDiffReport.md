# Naive Diff Experiment Report

## Background

This project studies compression methods for meteorological grid data. The
baseline storage used in this experiment is one tiled, LZW-compressed TIFF file
per timestamp.

The tested Naive Diff method stores each time series in blocks of 100 files:

- The first TIFF in each block is kept unchanged as a key frame.
- Each following TIFF in the same block stores:

```text
current_original - previous_original
```

For the May 2026 ERA5 2 m temperature dataset, this gives 744 files total,
split into 8 blocks. The first 7 blocks contain 100 files each, and the final
block contains 44 files.

## Why The Compression Gain Is Small

The temporal difference strongly reduces the numeric range and variance:

- Original temperature values range from about 197.81 K to 324.10 K.
- Difference values range from about -14.25 K to 15.04 K.
- Original variance is about 447.91.
- Difference variance is about 0.48.

However, the final storage size improves only slightly. The main reason is
that the implementation stores the differences as `float32` values. General
lossless byte compressors operate on the binary representation, not on the
semantic numeric range. A small floating-point value does not necessarily have
a compact or low-entropy byte representation:

- IEEE-754 `float32` always uses 32 bits per value.
- Nearby decimal values can still differ in many mantissa bits.
- Subtracting two `float32` fields may produce residuals with noisy low-order
mantissa bits.
- TIFF compressors such as LZW, Deflate, ZSTD, or LZMA can exploit repeated
byte patterns, but they do not automatically know that a small float residual
could be represented with fewer numeric bits.

So the Naive Diff transform is statistically useful, but storing the residuals
directly as `float32` leaves most of the per-value bit width unchanged.

## Compression Methods Better Suited To Small Floating-Point Residuals

For floating-point meteorological fields, the usual options are:

1. Quantize residuals to integers, then use integer compression.

   If a precision tolerance is acceptable, store:

   ```text
   int_residual = round((current - previous) / scale)
   ```

   The `scale` controls absolute precision. After quantization, bitpacking,
   delta coding, entropy coding, ZSTD, or LZMA can exploit the small integer
   range much more directly.

2. Store fixed-point integer temperatures before differencing.

   For example, store temperatures in centi-Kelvin or milli-Kelvin as integers,
   then difference the integer fields. This is effectively controlled
   quantization and makes bitpacking applicable.

3. Use floating-point-specific lossless compressors.

   Algorithms such as FPZIP or FPC predict floating-point values and encode the
   XOR or residual structure of the IEEE-754 bit patterns. They are designed to
   exploit smooth scientific floating-point arrays better than generic byte
   compressors.

4. Use lossy scientific compressors with error bounds.

   Algorithms such as ZFP and SZ can enforce absolute, relative, or precision
   error bounds. These are often much more effective for gridded scientific
   data when exact bitwise reconstruction is not required.

5. Shuffle or bitshuffle bytes/bits before generic compression.

   Byte shuffle or bitshuffle rearranges values so that sign/exponent/mantissa
   bytes or bits are grouped together. This can make ZSTD, LZ4, Deflate, or
   similar codecs more effective on floating-point arrays.

The most promising next step for this project is likely:

```text
temporal diff -> controlled quantization/fixed-point integer -> bitpacking or ZSTD
```

This directly uses the observed small residual range.

## Implementation

The Naive Diff implementation is in:

```text
src/naive_diff/naive_diff_tiff.py
```

The script reads TIFF files in timestamp order, writes one output TIFF per
input TIFF, and reports streaming statistics for both the original values and
the residual values.

The final command used for the full experiment was:

```bash
conda run -n grib python src/naive_diff/naive_diff_tiff.py \
  --output-dir data/ERA5-temperature-May2026-naive_diff \
  --compression lzma \
  --compression-level 9 \
  --overwrite
```

The output files are written to:

```text
data/ERA5-temperature-May2026-naive_diff
```

Data files are ignored by git via `.gitignore`.

## Compression Choice

Several TIFF codecs were tested on the first 100 frames:

| Codec | Size for first 100 frames |
| --- | ---: |
| Original LZW TIFFs | 161,601,423 bytes |
| Naive Diff + LZW | 208,771,055 bytes |
| Naive Diff + ZSTD level 9 | 170,245,454 bytes |
| Naive Diff + Deflate level 9 | 173,880,917 bytes |
| Naive Diff + LZMA | 159,687,708 bytes |

LZMA was the best among the tested TIFF codecs, so the final output uses TIFF
LZMA compression for diff frames. The TIFF metadata check showed:

```text
Compression: 34925 (LZMA)
Predictor: 3
TileWidth: 256
TileLength: 256
SampleFormat: IEEE floating point
```

## Full Dataset Results

Input:

```text
data/ERA5-temperature-May2026_tiffs
```

Output:

```text
data/ERA5-temperature-May2026-naive_diff
```

File counts:

| Metric | Value |
| --- | ---: |
| Total TIFF files | 744 |
| Key frames copied unchanged | 8 |
| Diff frames | 736 |
| Block size | 100 |

Storage size:

| Dataset | Size |
| --- | ---: |
| Original TIFF directory | 1,202,308,785 bytes |
| Naive Diff directory | 1,192,804,567 bytes |
| Reduction | 9,504,218 bytes |
| Reduction ratio | about 0.79% |

Original value statistics across all 744 original TIFFs:

| Statistic | Value |
| --- | ---: |
| Count | 772,450,560 |
| Min | 197.810302734375 |
| Max | 324.1005859375 |
| Mean | 280.0082946833511 |
| Variance | 447.9062597971049 |

Diff value statistics, excluding the 8 key frames:

| Statistic | Value |
| --- | ---: |
| Count | 764,144,640 |
| Min | -14.251800537109375 |
| Max | 15.040283203125 |
| Mean | 0.0026180964021852667 |
| Variance | 0.4830509629461122 |

## Conclusion

Naive Diff confirms strong temporal continuity in the ERA5 2 m temperature
field. The residuals have a much smaller range and variance than the original
values.

As currently stored, however, the residuals are still `float32` TIFF images.
This means each residual keeps a 32-bit floating-point representation, and the
generic TIFF compressors can only exploit byte-pattern redundancy. The final
lossless storage reduction is therefore small: about 0.79%.

The experiment suggests that temporal differencing is a useful predictor, but
it should be paired with a representation that actually reduces residual bit
width. The next experiment should convert residuals to fixed-point integers
with an explicit precision scale, then test bitpacking and ZSTD/LZMA on those
integer residuals.
