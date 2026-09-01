# HDF5 introduction

## Basic Information

### 技术概述

HDF5，全称：Hierarchical Data Format 分层数据格式，包含一整套用于存储和管理数据的数据模型、库和二进制文件格式。主要特色在于提供多维数据存储以及自定义数据类型的格式存储，支持一个文件的多个数据块并发的读写，在数据存储以及数据读写上有不错的表现。



### 发展历史

- 提出：1987年 NCSA\(美国国家超级计算应用中心\) 的 GFTF 小组 提出 HDF

- 设计初衷：实现一种架构无关的文件格式，满足NCSA在使用多种不同计算平台之间移送科学数据的需求；支持存储管理大型对象\(理解为数据的类对象\)，能够在一个对象\(hdf5的逻辑对象\)内存储不同类型数据

- 发展演变：

    - 1996年：为了处理TB级数据集和并行文件处理的需求，NCSA 与 NASA 和 DOE 合作，结合了 异步IO技术 ，发展了支持并行I/O的新格式。

    - 1999年：为了处理NASA EOS数据和信息系统中的大量数据产品，HDF4成为其关键部分，用于收集、创建、存储和分发超过5PB的数据。

    - 2007年：为了合并两种科学数据格式\( HDF 和 netCDF \)的优势，简化数据处理流程，netCDF和HDF5格式进行统一，以HDF5作为netCDF的基础格式



### 广泛的应用

- 科学数据存储，例如多维矩阵存储，气象数据存储\(网格数据\)（没有接触过实际案例）

- 设备传感器数据存储，例如南航，结构如下：

- 粒子仿真数据存储，例如两大知名的蒙特卡洛仿真程序Geant4和MCNP分别在19年和22年支持了HDF5格式，其中MCNP的记录结构如下。\(实际粒子实验数据，NOvA中微子震荡实验，机器产出的是HDF5文件\)



## Technology Introduction

### 数据模型

数据模型是用户视角下的数据组织结构，是用户描述数据、数据类型与组织形式的主要参考。HDF5的数据模型概念，按照范围从大到小，依次为File，Group，Dataset三级，其中File是指一个HDF5文件，Group是中间层级节点，下面可以链接group或者dataset

HDF5的数据模型，以及与TsFile的数据模型类比

在HDF5文件的File层级，用一棵 以Group为内部节点，Dataset为叶节点 的树 来组织整个文件的数据信息。

group的层级结构，用于表示Dataset之间的联系，方便用户根据dataset之间的联系组织数据。例如存储有粒子仿真数据的数据集，放置在同一个group下，而存储有粒子数据绘制的直方图的数据集，则可以选择与前者不放置在同一个group下;或者是存储有航班元数据信息的数据集，存放在同一个group下，可以将同一个设备或者同一个类别的参数放在同一个group下。

而Dataset是记录数据的结构，有数据空间、数据类型、属性以及实际数据四个部分组成与维护。数据data是由一系列数据元素/单元格组成，这些数据的维数以及每个维的大小由dataspace决定，每个单元格所存储的数据类型由datatype决定，同时类型也决定了写入磁盘的方式，而属性则是记录data的统计或者辅助信息

（tips：datatype一个dataset仅有一个，所以常看见的二维表结构，其实是复合数据类型配合一维结构组成）



### 文件格式

详细格式细节可以参考我结合实际文件分析整理出的文档：[HDF5文件逐字节分析——以南航HDF5文件为例](https://apache-iotdb-project.feishu.cn/docx/KrYCdseyhoo76SxN2ifceO3Nnc3)

HDF5的文件格式定义了HDF5在磁盘上的存储结构。HDF5向用户呈现高级对象（group 和 dataset）。然而，在底层信息实际写入磁盘时，一个HDF5文件由更低级的对象组成。HDF5库使用这些低级对象来表示高级对象，然后通过API呈现给用户或应用程序。HDF5的文件格式概念从大到小可以划分为三级，分别是文件元数据superblock，Group的组织信息\(b\-tree,local heap\)，Dataset的元数据\(header message\)与数据信息\(data\)。



整体架构 \& 格式对比

路径索引对比

数据索引对比 \& 数据格式对比

文件的元数据，记录有文件的一些版本信息，以及整个文件的一些全局信息，例如地址长度，文件起始和终止信息等。记录有根group的起始信息。

父group想要找到子group，就可以通过父group的b\-tree来检索，这里的key是子group的name link在local heap当中的偏移量。如果想要找到子group的子group，就需要通过两次b\-tree。

这里还有global heap，是用于专门存储变长数据的地址，没有全局索引，只有使用到的地方才有这个global heap的地址信息。在一个file当中会有很多类似这样的地址段\(hdf5称其为集合Co\)，这些地址段的全集称为global heap

对于数据层，主要是有一些header message来组成，其中常用的有如图的五个message。其中continue message是当前段不够记录，就记录一个延伸的地址。dataspace则是记录data的多维信息。而datatype有11种类型，提供了丰富的选择，其中0x05类型是自定义类型。最关键的一个message是layout message，这个会存储类型，并记录有数据存储的位置。这里有三种存储类型，compact，就是直接存在layout message上，continue，就是给个地址，连续存储。特色的chunked，layout的address会指向一个b\-tree，这个b\-tree和前面group索引group的b\-tree结构式一样的，这里是用来索引数据块，记录有很多数据块的地址信息。

对于HDF5默认使用的是上述的结构即 superblock version0，而version1在0上的修改很小，只有部分字段不一样。而version2 则是更新了几个新的结构，例如对于空闲块的索引记录 freeSpace\-index，对local heap和global heap的优化 Fractal heap，在之前的基础上加了个索引b\-tree，加快变长变量的获取。在消息类型上，进阶版本的 attribute info message 可以一次记录多个attribute，不用每次都写一堆message，然后这些记录也是在fractal heap里面。（南航用的是version 0）



和tsfile相似与区别

- 每个层级都有下一个层级的b\-tree索引，tsfile的key直接写在对应位置上，而HDF5则是额外存在一个local heap当中。不过，tsfile只有table到device，device到measurement两层索引结构。而HDF5的group结构是可以一直嵌套的。（和schema的pbtree很接近）

- 对于数据的描述，tsfile这边是额外记录了一些数据的统计信息，而HDF5更加专注于描述数据类型

- 在记录顺序上，tsfile是从后往前读，索引在后，数据在前，整齐排列。HDF5则是从前往后读，但是这些次序主要是依赖于b\-tree的地址映射，实际存储分布上，数据和索引混合在一起的。所以同样的数据写HDF5可能有细小的差别，就是分布不同，对齐需要的0字符不同。



实际案例分析：



## Visualization Tools

- web：https://myhdf5\.hdfgroup\.org/ 

- app：hdf5viewer \(官网https://github\.com/HDFGroup/hdfview/releases/tag/v3\.3\.2\)

- Linux tool: h5dump

```Bash
// 安装
sudo apt install hdf5-tools
h5dump --version

// 使用
h5dump file
```



## Api Using

### C/C\+\+

目前C环境仅仅在linux上完成配置，在windows上配置存在不少问题没有解决。

配置主要参考了https://blog\.csdn\.net/jdhcb/article/details/140199347

目前在Fit8上已经配置有HDF5的依赖包，可以直接使用。

参考代码如下：

\[hdf5\_read\_test\.cpp\]

\[hdf5\_write\_test\.cpp\]



下面是其编译指令。

```bash
g++ hdf5_write_test.cpp -o hdf5_write_test -I/usr/local/hdf5/include -L/usr/local/hdf5/lib -lhdf5
./hdf5_write_test

g++ hdf5_read_test.cpp -o hdf5_read_test -I/usr/local/hdf5/include -L/usr/local/hdf5/lib -lhdf5
./hdf5_read_test
```



### Python

这是使用最为方便的方式，在命令行直接使用 pip install h5py即可使用。

参考代码如下：下面是将HDF5文件读取出来后，以无压缩的形式写成为一个新的HDF5文件的过程

\[temp\.py\]



### Java

HDF5官方的java库最近一次更新在2010年，上手尝试后发现在环境配置上有很多问题没有解决。最终选择的是第三方的开源的java库，称为jhdf，链接如下：https://github\.com/jamesmudd/jhdf?tab=readme\-ov\-file

Maven依赖如下：

```xml
<dependency>
    <groupId>io.jhdf</groupId>
    <artifactId>jhdf</artifactId>
    <version>0.9.0</version>
</dependency>
```

参考代码：

- https://github\.com/jamesmudd/jhdf/blob/master/jhdf/src/main/java/io/jhdf/examples/ReadDataset\.java

- https://github\.com/jamesmudd/jhdf/blob/master/jhdf/src/main/java/io/jhdf/examples/WriteHdf5\.java



## Reference

- https://github\.com/HDFGroup/hdf5?tab=readme\-ov\-file

- [HDF5: HDF5 File Format Specification Version 2\.0](https://support.hdfgroup.org/documentation/hdf5/latest/_f_m_t2.html)

\[An overview of the HDF5 technology suite and its applications\.pdf\]

\[A case study on parallel HDF5 dataset concatenation for high energy physics data analysis\.pdf\]





