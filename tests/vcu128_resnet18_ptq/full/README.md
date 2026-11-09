# VCU128：完整 ResNet18 单核逐层验证

前提：ResNet18 离线整数参考已通过；hart0、hart1 各自的 40 项算子微测试均已通过。
本阶段把算子接成完整网络。先运行 hart0，再复位运行 hart1；本包不启动双核并行。

## 1. 本阶段到底验证什么

每张图片执行 30 个连续检查点：20 次卷积调用（第一层融合最大池化）、8 次残差加法、1 次全局平均、1 次 FC。
第一层直接检查 `stem.pool`，没有单独输出 `stem.conv_relu`；残差 skip 对齐在 residual 算子内部完成，没有额外 skip_aligned 检查点。

除了第一层使用原始 INT8 图片输入，后续算子的输入指针全部指向板上已计算的中间张量。
golden 数据只由校验逻辑读取，不会成为后续算子的输入。投影分支使用该 BasicBlock 的真实入口张量。

首版采用 30 块独立缓冲区，每块前后各 64 B 保护区；不做激活缓冲区复用。
各图片顺序复用同一套 30 块缓冲区，共享同一份只读权重。

每个检查点：Gemmini 计算 → Rocket 严格字节比较 → 暂停 WAIT_HOST → Host 完整读回比较 → ACK → 下一层。
每张图片结束后，Host 再次读回全部 30 块中间张量，确认保留结果和保护区。
整个运行结束后，Host 检查完整只读 blob 没有变化，并保存每张图的完整 1000 个 INT32 logits。

这是带逐层握手的正确性验证程序。日志中的 cycles 是单次后端调用范围内的周期数；其总和不等于不间断推理 makespan。
先通过数值验证，再做连续执行和双核性能测量。

## 2. 目录与环境

| 位置 | 工作 | 环境 |
|---|---|---|
| `/home/cuixh/model_projects/resnet18_gemmini_ptq` | 已有量化参数和整数 golden | 无需重新量化 |
| `/home/cuixh/chipyard/tests/vcu128_resnet18_ptq/full` | 导出图、编译 ELF、打包 | 导出用 `.venv`；编译先 source Chipyard env.sh |
| `.../resnet50/software/resnet18_full` | XDMA 加载、START、结果校验 | Host `.venv` |

这里新建 `full` 和 `resnet18_full`，不会覆盖之前通过的 `qops` 和 `resnet18_qops`。
硬件继续使用此前已通过的双 Rocket＋双 Gemmini ResNet50 比特流，不需要重新综合实现。

## 3. 安装源码

将 `vcu128_resnet18_full_software.tar.gz` 上传至 `/home/cuixh/`：

```bash
cd /home/cuixh/chipyard
tar -xzf /home/cuixh/vcu128_resnet18_full_software.tar.gz
```

源码包只包含源码、说明和兼容 RoCC 编码头，不包含交付方的量化权重、golden 或 ELF。

## 4. 从你已有的量化结果导出完整网络

```bash
source /home/cuixh/.venv/bin/activate
cd /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/full

python prepare_full.py \
  --model-root /home/cuixh/model_projects/resnet18_gemmini_ptq \
  --samples all \
  --out generated
```

导出需要 NumPy；不运行 PyTorch，不重新校准或量化。
脚本检查量化文件、整数参考脚本、各张图的节点数据 hash，以及固定 ResNet18 的层数、形状和图连接。

若使用原来的 4 张图，预期：

```text
FULL GRAPH EXPORT PASS: 4 samples; 120 chained checkpoints; ...
```

也可先只选择一张：`--samples 0`。选择其他任务编号用 `--samples 1`，或 `--samples 0,1`。
每次改变选择，都要重新编译两份 ELF 并重新打包，不能与旧 manifest/blob 混用。

生成 `generated/full_blob.bin`、`full_manifest.json`、`full_cases.c`、`full_generated.h`、`full_blob.S`。
`.incbin` 将输入、权重、偏置和检查用 golden 直接放入 ELF 只读段，避免数百万项文本数组。

## 5. 在 Chipyard 环境编译

```bash
deactivate
cd /home/cuixh/chipyard
source ./env.sh
hash -r
command -v riscv64-unknown-elf-gcc

cd tests/vcu128_resnet18_ptq/full
make HART=0 PYTHON=/home/cuixh/.venv/bin/python
make HART=1 PYTHON=/home/cuixh/.venv/bin/python
```

`PYTHON` 只用于数据/ELF检查；C 编译器来自已加载的 Chipyard 工具链。
Makefile 会先检查 `stdio.h`/`stdlib.h`。若检查失败，先恢复此前成功编译微测试的工具链，不向 RISC-V 编译器添加宿主机 `/usr/include`。
驱动头文件继续固定在每份构建的 `inputs/` 中，沿用已验证的 DIM=8 配置和 Gemmini API。

输出：

```text
build/hart0/resnet18_full_hart0.elf
build/hart1/resnet18_full_hart1.elf
```

每次构建自动检查 RV64/lp64d、入口/栈/BSS、完整 blob、图描述符和 live tensor 指针。
ELF 按 mhartid 仅允许指定的一个核进入 C；另一核停在 WFI。

可选的离线检查：

```bash
make HART=0 check PYTHON=/home/cuixh/.venv/bin/python
make HART=1 check PYTHON=/home/cuixh/.venv/bin/python
```

有宿主机 GCC 时，可选运行独立 C 标量整图验证：

```bash
make HART=0 native-check PYTHON=/home/cuixh/.venv/bin/python
```

它连续执行实际图连接，使用 INT64 标量卷积、C 缩放宏等计算并逐层检查；不执行 Gemmini，不代表 FPGA 通过。

## 6. 打包并复制到 Vivado 工程 software

两份 ELF 都成功构建后：

```bash
make host-bundle PYTHON=/home/cuixh/.venv/bin/python

mkdir -p /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet50/software/resnet18_full

tar -xzf dist/resnet18_full_host.tar.gz \
  -C /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet50/software/resnet18_full
```

包内包含 Host 所需 Python、两份 ELF、绑定的 manifest/blob 和说明，不再需要从 Chipyard 零散挑选文件。
打包器拒绝缺少某份 ELF 或 ELF 与当前数据不一致的情况。

## 7. 在板上验证 hart0

先执行你此前已验证的全通路复位流程，确认本轮一次性 START 尚未使用。
不要向未经验证的 `0x10` 软件复位寄存器写入。

回到 Host 环境：

```bash
conda deactivate
source /home/cuixh/.venv/bin/activate

cd /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet50/software/resnet18_full
python -m pip install -r requirements.txt

python host_resnet18_full.py \
  --elf build/hart0/resnet18_full_hart0.elf \
  --manifest generated/full_manifest.json \
  --hart 0 \
  --timeout-sec 1800 \
  --report reports/full_hart0.json
```

原来的 4 张图全部通过时，最后显示：

```text
PASS RESNET18 FULL HART0: 4 samples, 120 chained checkpoints; all INT32 logits exact
```

每张图结束打印从实测 logits 得到的 Top-1。完整 Top-5、1000 个 INT32 logits、输入来源标识、每层周期和 ELF/量化身份记录保存在报告中。
若失败，脚本给出 task/节点、首个不同元素及实际/预期值；不会发 ACK 跳过错误层，也不会自动改 golden。
失败后结果缓冲区会保留，但目标等待 ACK 有超时，需及时保留诊断输出。重新运行完整 Host 前应重新复位。

## 8. 验证 hart1

hart0 完成后，再次执行全通路复位，确保 START 可用：

```bash
python host_resnet18_full.py \
  --elf build/hart1/resnet18_full_hart1.elf \
  --manifest generated/full_manifest.json \
  --hart 1 \
  --timeout-sec 1800 \
  --report reports/full_hart1.json
```

预期为 HART1 的同样通过信息。两次仍是各单核顺序推理，不是双核并行。
两核完整网络都通过后，再进入双实例并行推理与相同工作量性能对比。

## 9. 地址与实现边界

APP 使用 CPU/FBus 地址 `0x80000000`～`0x8f000000`；栈为 `0x8fe00000`～`0x8ff00000`；状态在 `0x8ff00000`，日志在 `0x8ff10000`。
Host 沿用已验证的 XDMA/FBus 访问方式，不自行转换为原始 HBM 偏移。
网络权重、输入、golden 在只读 blob；全部激活在 BSS arena，ELF 符号和 manifest 共同约束其地址。

首版保留了微测试阶段的小行数 residual 显式 mvin/mvin2/mvout 路径；ResNet18 的真实残差行数均不小于 49，仍使用已通过真实模型微测试的常规路径。
不能据此声称原来的 I=1,J=9 loop-resadd RTL 根因已经排除。

当前程序仅支持固定的量化 ResNet18、batch=1、NHWC、INT8 权重/激活、INT32 bias/FC、每层标量 scale 和已冻结的残差量化合同。
本阶段不会读取 JPEG；输入来自你已有的量化参考。自选新图片需先通过同一固定预处理/输入量化规则生成输入和 golden。
