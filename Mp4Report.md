# ERA5 to MP4 compression report

The temperature fields were mapped over one global dataset range to 8-bit grayscale, then encoded as YUV 4:2:0 MP4. This is a lossy representation.

- Frames: 744 at 24 fps
- Grid: 721 x 1440
- Temperature range: 197.810303 to 324.100586 K (126.290283 K span)
- Raw float32 tensor: 3,089,802,240 bytes
- Input TIFF files: 1,202,308,785 bytes
- Input GRIB: 1,544,981,472 bytes

| Profile | MP4 bytes | GRIB ratio | TIFF ratio | raw-f32 ratio | Encode s | fps | MAE K | RMSE K | Max K | PSNR dB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| h264_crf18 | 7,345,535 | 210.33x | 163.68x | 420.64x | 12.81 | 58.07 | 0.3472 | 0.4783 | 9.5264 | 48.43 |
| h264_crf23 | 3,592,665 | 430.04x | 334.66x | 860.03x | 11.89 | 62.55 | 0.4262 | 0.6036 | 11.9293 | 46.41 |
| h264_crf28 | 1,852,811 | 833.86x | 648.91x | 1667.63x | 12.01 | 61.95 | 0.5388 | 0.7796 | 17.9565 | 44.19 |
| h265_crf28 | 810,476 | 1906.26x | 1483.46x | 3812.33x | 14.15 | 52.60 | 0.5696 | 0.7906 | 16.2277 | 44.07 |

`ratio = source bytes / MP4 bytes`; larger is more compression.

Machine-readable results are in `results.json` beside the MP4 files.
