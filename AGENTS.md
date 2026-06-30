magic number: 2311

执行python程序的默认环境选择utils。执行：

conda activate utils

来启动这个环境。

对于涉及grib,zarr等类型的数据的读取和解析的时候，你可能需要使用

conda activate grib

来启动这个专门用于解析grib的环境。该环境安装了：

conda install xarray cfgrib eccodes dask netcdf4 matplotlib numpy pandas jupyterlab

python -m ipykernel install --user --name grib --display-name "Python (grib)"

conda install zarr numcodecs

如果conda环境出现问题，也可以选择使用无状态的执行方式：

conda run -n utils python script.py
conda run -n grib python script.py