# 3-dim-diff-zarr-hdf5

本项目验证把 chunk-local 三维整数差分作为应用层预变换后，实际写入 Zarr 和
HDF5 能否获得无损文件大小收益。

变换链为：

```text
float32 bit pattern → uint32 modular 3-D delta → optional ZigZag
→ optional region reorder → optional bitshuffle → Blosc-Zstd
```

每个 chunk 独立变换，三维减法和逆累加都使用模 2^32 算术。输出文件物理
dtype 为 `uint32`，解码后与输入 float32 位模式逐元素相同。

完整运行：

```bash
conda run -n utils python src/3-dim-diff-zarr-hdf5/benchmark.py --overwrite
```

脚本默认对原始、低 7 位白化和低 8 位白化三组数据执行六种变换消融，随后
用平均最好的模式扫描时间 chunk 24、48、96。大体积输出写入
`data/3-dim-diff-zarr-hdf5/`，报告写入
`reports/ThreeDimDiffZarrHdf5-cn.md`。

这还是应用层编码原型，不是透明 codec。普通客户端直接读取数据集时会看到
编码后的 `uint32`，必须调用本项目的逆变换才能恢复温度。
