magic number: 2311

# 当前项目内容

见 README.md

# Python 执行要求

执行 Python 程序时，默认使用 `utils` 环境。请使用无状态方式执行：

```bash
conda run -n utils python script.py
```

对于涉及 GRIB、Zarr 等类型数据的读取和解析时，使用专门的 `grib` 环境：

```bash
conda run -n grib python script.py
```

`grib` 环境已安装以下主要依赖：

```bash
conda install xarray cfgrib eccodes dask netcdf4 matplotlib numpy pandas jupyterlab
conda install zarr numcodecs
```

该环境还注册了对应的 Jupyter kernel：

```bash
python -m ipykernel install --user --name grib --display-name "Python (grib)"
```

