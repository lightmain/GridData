# ERA5 float32 byte-channel FFV1 report

Each little-endian float32 value is mapped byte-for-byte to one BGRA pixel: B stores bits 0-7, G stores 8-15, R stores 16-23, and A stores 24-31. FFV1 compresses these four 8-bit channels losslessly in a Matroska container.

- Frames: 744 at 24 fps
- Grid: 721 x 1440
- Raw float32 tensor: 3,089,802,240 bytes
- Input TIFF directory: 1,202,308,785 bytes
- Input GRIB: 1,544,981,472 bytes

| Profile | MKV bytes | Raw ratio | TIFF ratio | GRIB ratio | Encode s | MiB/s | Verify s | Bitwise |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| ffv1_rice_context0 | 2,255,177,664 | 1.370x | 0.533x | 0.685x | 14.41 | 204.49 | 9.69 | yes |
| ffv1_range_context1 | 2,173,908,250 | 1.421x | 0.553x | 0.711x | 33.51 | 87.94 | 15.99 | yes |

`ratio = source bytes / MKV bytes`; values above 1 mean the FFV1 file is smaller.

This representation preserves float32 bits but is a custom scientific-data convention, not a standard semantic mapping understood by ordinary video players. Decoding must preserve BGRA and reconstruct little-endian float32 values.

Machine-readable results are stored in `results.json` beside the MKV files.
