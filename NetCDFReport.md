# ERA5 NetCDF-4 lossless compression report

All codecs store the original float32 bit patterns without quantization. Each output was read back chunk-by-chunk and compared bitwise with its source TIFF.

- Shape: 744 x 721 x 1440
- Chunk shape: 24 x 128 x 256
- Raw float32 tensor: 3,089,802,240 bytes
- Input TIFF directory: 1,202,308,785 bytes
- Input GRIB: 1,544,981,472 bytes

| Codec | NetCDF bytes | Raw ratio | TIFF ratio | GRIB ratio | Write s | Write MiB/s | Verify s | Verify MiB/s | Bitwise |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| zlib_6_shuffle | 1,067,369,740 | 2.895x | 1.126x | 1.447x | 155.62 | 18.93 | 13.18 | 223.59 | yes |
| zstd_5 | 1,620,009,494 | 1.907x | 0.742x | 0.954x | 33.72 | 87.38 | 9.33 | 315.96 | yes |
| bzip2_6 | 1,333,175,396 | 2.318x | 0.902x | 1.159x | 255.60 | 11.53 | 71.25 | 41.36 | yes |
| blosc_lz4_5_shuffle | 1,424,784,583 | 2.169x | 0.844x | 1.084x | 8.64 | 341.03 | 7.57 | 389.01 | yes |
| blosc_zstd_5_shuffle | 1,089,132,495 | 2.837x | 1.104x | 1.419x | 47.16 | 62.49 | 7.90 | 373.12 | yes |

`ratio = source bytes / NetCDF bytes`; values above 1 mean the NetCDF is smaller.

Verification timing includes reading both the NetCDF output and the source TIFFs; it is not a pure NetCDF read benchmark.

Plugin codecs such as Zstandard, Bzip2, and Blosc require the corresponding HDF5 filter plugin when the file is read. Deflate/zlib has the broadest interoperability.

Machine-readable details, including active HDF5 filters, are stored in `results.json`.
