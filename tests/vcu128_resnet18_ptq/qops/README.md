# VCU128 ResNet18 Gemmini 算子微测试

用于已经跑通 ResNet50 的 VCU128 双 Rocket + 双 Gemmini 8x8 WS 工程。
模型离线量化与四张图的整数参考通过后，先验证这里的算子，再连接完整网络。
本轮分别运行 hart0 和 hart1，不需要先实现新的双核并发协议。

## 1. 当前硬件与验证目标

继续使用已验证的 `VCU128DualRocketDualGemminiResNet50Config` 比特流：8x8 WS、INT8/INT32、SP 64 KiB、ACC 32 KiB、64-bit DMA、max-pool 开启、FP32 MVIN/ACC scaling、ACC full-width read。
软件会检查现有 `gemmini_params.h` 和之前使用的 Gemmini C API 版本，并复制到本次构建目录固定下来。
发现版本不同会报错，先分析差异，不改断言硬凑通过。

目标是每项输出的每一个字节都与冻结整数参考一致；不仅看 Top-1、checksum 或“没有卡住”。
Host 同时检查输出前后保护区、运行编号、hart、用例序号、完成数量和 ELF/数据绑定。

## 2. 将源码放入 Chipyard

上传 `vcu128_resnet18_qops_software.tar.gz` 到 `/home/cuixh/`，执行：

```bash
cd /home/cuixh/chipyard
tar -xzf /home/cuixh/vcu128_resnet18_qops_software.tar.gz
```

源码位置：

```text
/home/cuixh/chipyard/tests/vcu128_resnet18_ptq/qops
```

包中只包含源码、说明和兼容指令编码头文件，不包含交付方生成的权重、测试数据或预编译 ELF。

## 3. 在现有 .venv 生成自己的测试数据

```bash
source /home/cuixh/.venv/bin/activate
cd /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/qops

python prepare_qops.py \
  --model-root /home/cuixh/model_projects/resnet18_gemmini_ptq \
  --sample 0 \
  --out generated
```

这一步需要 NumPy，使用之前安装 PyTorch 时的 `.venv` 即可；不需要重新运行量化。
它会校验模型参数/参考文件的哈希，抽取 sample_0000 的真实整数输入、权重和中间结果，重新计算被选中的算子并与已有 golden 核对。
当前尺度计划本地生成 40 项；若其他计划中尺度合并情况不同，最终数量以脚本输出和绑定的 manifest 为准。

生成文件：

| 文件 | 内容 |
|---|---|
| `generated/qops_blob.bin` | 64 B 对齐的输入、权重、偏置和预期输出 |
| `generated/qops_manifest.json` | 用例名称、参数、偏移、字节数、形状及来源哈希 |
| `generated/qops_cases.c` | 供目标程序调用的静态算子描述 |
| `generated/qops_generated.h` | 用例数量、最大输出尺寸 |
| `generated/qops_blob.S` | 将上述原始二进制直接链接为 ELF 的只读数据 |

没有把几百万个数打印成 C 文本；这里使用 `.incbin` 把相同整数数据放入 `.rodata`。加载 ELF 时数据也随 PT_LOAD 一起写入 HBM。

## 4. 用 Chipyard 工具链编译两份 ELF

如果当前激活了 `.venv`，先执行 `deactivate`，然后切换 Chipyard 环境：

```bash
conda activate /home/cuixh/chipyard/.conda-env
cd /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/qops

make HART=0 PYTHON=/home/cuixh/.venv/bin/python
make HART=1 PYTHON=/home/cuixh/.venv/bin/python
```

`PYTHON` 用现有 `.venv` 运行构建检查；RISC-V GCC 仍从 Chipyard `.conda-env` 的 PATH 查找。
如果这份 `.venv` 缺少 pyelftools，执行 `/home/cuixh/.venv/bin/python -m pip install pyelftools`。
NumPy 不用于交叉编译步骤，只在第 3 步生成测试数据时使用。

输出：

```text
build/hart0/resnet18_qops_hart0.elf
build/hart1/resnet18_qops_hart1.elf
```

每次 make 都会冻结并核对硬件头文件，构建后自动做 `--dry-run`：检查 RV64/lp64d、入口、内存、堆栈、构建元数据及测试数据的字节绑定。
程序按 mhartid 仅允许 ELF 指定的核进入 C，其余核停在 WFI；因此两个核没有同时清 BSS 或写同一状态块的问题。

可选离线 Host 检查：

```bash
make HART=0 check PYTHON=/home/cuixh/.venv/bin/python
make HART=1 check PYTHON=/home/cuixh/.venv/bin/python
```

有主机 GCC 时可选独立 C 标量验证，不执行 Gemmini：

```bash
make native-check HART=0 PYTHON=/home/cuixh/.venv/bin/python
```

## 5. 用例覆盖

| 类别 | 具体内容 |
|---|---|
| ACC 重定标 | 实际模型各个 M、Identity 分支尺度、1/49、1/2、1；bias-only GEMM，含负值、ties、饱和、2^24 和 INT32 边界，J=31 尾列 |
| 重定标 + ReLU | 同一批边界值，scale=1/2，检查负值置零位置 |
| INT32 全宽读出 | 零乘积加大偏置，必须逐位保持 INT32，不能经过普通 FP32/INT8 输出路径 |
| 首层卷积 | 真实 224x224x3 -> 112x112x64，7x7 s2，BN 已折叠，ReLU |
| 融合最大池化 | 同一首层卷积加 3x3 s2 p1 -> 56x56x64 |
| 3x3 s1 | 真实 layer1.0.conv1，有 ReLU |
| 3x3 s1 无 ReLU | 真实 layer1.0.conv2，保留残差汇合前的有符号结果 |
| 3x3 s2 | 真实 layer2.0.conv1，56x56x64 -> 28x28x128 |
| 投影分支 | 真实 layer2.0.downsample.0，1x1 s2 |
| 残差 | 真实 Identity 分支与投影分支各一项，另有 signed ties/saturation 合成向量 |
| 全局平均 | 真实 7x7x512，以及 9 通道边界输入 |
| FC | 真实 1x512 乘 512x1000 + INT32 bias，全宽 INT32 输出 |

目标算子全部通过 Gemmini WS 接口执行。边界用例的预期值在 Host 离线生成，不靠目标 CPU 计算出“正确答案”。
目标 CPU 会比较 accelerator 输出与 ELF 中的预期数据；Host 再独立读回完整输出，与自己持有的绑定数据做比较。

这不是对所有张量形状或所有舍入值的穷举证明。它覆盖新模型所需的关键路径和已知边界；完整模型及双核并发仍是后续验证阶段。

## 6. 在板上先测试 hart0

确认板卡运行的是此前 ResNet50 工程的比特流，并先完成之前已经验证过的“全通路复位、START 未使用”的标准准备流程。
脚本会检查控制器 ID=0x534f4301、STATUS ready bit 和一次性 START 标志；不会写入任何未经验证的软件复位寄存器。

回到 `.venv`，先可选检查地址通路（不发 START）：

```bash
source /home/cuixh/.venv/bin/activate
cd /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/qops

python host_resnet18_qops.py --probe-only
```

然后运行 hart0：

```bash
python host_resnet18_qops.py \
  --elf build/hart0/resnet18_qops_hart0.elf \
  --manifest generated/qops_manifest.json \
  --hart 0 \
  --timeout-sec 1800
```

Host 会探测地址、写入并读回 ELF、准备状态和保护区、发出 START。
每项完成后，目标停在 WAIT_HOST，保持输出缓冲区稳定；Host 读回并核对所有字节和保护区后，才写 ACK 让目标进入下一项。

预期逐项输出例如：

```text
PASS hart0 ... requant_...
PASS hart0 ... stem_7x7_s2_relu ...
...
PASS hart0 ... fc512x1000_full_int32_real ...
PASS RESNET18 QOPS HART0: 40 cases; Host compared every output byte and guards
```

如果某项不一致，先停在该项分析；保持原有数值合同，不把容差统一改成 +/-1。
输出有陷阱时会打印 mcause/mepc/mtval；实际对比失败会给出用例名和首个错误字节位置。

## 7. 再测试 hart1

hart0 测试使用了一次性 START。先重新完成此前验证过的全通路复位准备，再运行：

```bash
python host_resnet18_qops.py \
  --elf build/hart1/resnet18_qops_hart1.elf \
  --manifest generated/qops_manifest.json \
  --hart 1 \
  --timeout-sec 1800
```

预期末行：

```text
PASS RESNET18 QOPS HART1: 40 cases; Host compared every output byte and guards
```

这两次分别验证每个 Rocket 能驱动其 Gemmini 完成同一批计算。双核同步通信已在上一个实验验证；算子通过后再整合完整模型并发。

## 8. 如果要放入 Vivado 工程 software 目录

可以先在 Chipyard 的本目录运行 Host，无需移动文件。
需要迁移时，在两份 ELF 都已成功构建后执行：

```bash
make host-bundle PYTHON=/home/cuixh/.venv/bin/python
```

得到 `dist/resnet18_qops_host.tar.gz`。将它解压到目标工程的 `software/resnet18_qops/` 后，在该目录执行第 6～7 步同样的 Host 命令。
包中携带两份 ELF、绑定的 manifest/blob 及 Host 所需脚本，不能只复制 ELF 而漏掉 `generated/`。
打包前会检查两份 ELF 与当前生成数据一致，防止只重建了一个核而混用旧数据。

## 9. 软件与内存结构

| 文件/区域 | 作用 |
|---|---|
| `prepare_qops.py` | 用自己的量化结果生成固定测试数据 |
| `freeze_headers.py` | 固定兼容的本地 Gemmini 驱动和硬件参数 |
| `start.S` | 选择 hart、建立栈/陷阱入口、启用 F/D、清 BSS |
| `qops_backend.c` | 调用 Gemmini 的 matmul/conv/resadd/global average |
| `runtime.c` | 输出保护区、逐项状态、日志、Host ACK 握手 |
| `host_resnet18_qops.py` | ELF 绑定检查、加载、START、逐项完整输出验证 |
| `protocol.py` | Host 状态 ABI 与地址约定 |
| `xdma_transport.py` | 复用已验证的 XDMA CPU/FBus 地址访问方式 |
| APP 0x80000000～0x8f000000 | 程序、只读 fixture、BSS 输出工作区 |
| 0x8fe00000～0x8ff00000 | 当前被选中核的栈 |
| 0x8ff00000 | 外部状态块，Host 初始化并保留 run_id |
| 0x8ff10000 | 64 KiB 日志 |

这些是 CPU/FBus 地址，Host 不自行减去 0x80000000 改成原始 HBM 偏移。
输出缓冲区在 APP BSS 中，Host 从符号与运行时结果交叉核对其地址；不硬编码临时数据位置。

## 10. 交付前验证及边界

见 `VALIDATION.md`。已做源码数据重算、独立 C 标量验证、RV64 交叉编译、两核 ELF 离线检查和 Host 错误拒绝测试。
本软件尚未由交付方在用户 VCU128 上执行，不能把这些离线检查当作板上通过。
两核算子均通过后，下一阶段是单核完整 ResNet18 逐层/最终输出验证，再到双核各跑模型。
