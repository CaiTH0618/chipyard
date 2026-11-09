# VCU128：完整 ResNet18 双 Rocket＋双 Gemmini 并行测试

前提：两核分别通过 40 个算子 case 和完整 ResNet18 的四图/120 个检查点。
本包沿用同一硬件、同一算子后端和量化数据，新增两个核同时运行完整模型，以及同工作量串行对照。
交付前验证是本地离线验证；Gemmini 真板并行结果需要按下述流程执行后确认。

## 1. 任务分配与验收

默认使用 `full/generated` 内原有四张图：

| 模式 | hart0＋Gemmini0 | hart1＋Gemmini1 |
|---|---|---|
| `--mode serial` | task0、task1、task2、task3 顺序执行 | 参与启动同步，等待计算结束，不调用模型 |
| `--mode dual` | task0、task2 顺序执行 | 同时处理 task1、task3 |
| `--mode dual --swap` | task1、task3 顺序执行 | 同时处理 task0、task2 |

分配依据是所选任务列表的位置，不是 task_id 数值；例如之前选 `--samples 2,3`，正常并行由 hart0 跑 task2、hart1 跑 task3。

一张图仍有 30 个真实连接的算子步骤：20 Conv（首层融合最大池化）、8 Add、1 GAP、1 FC。
这次两核各自连续执行自己的完整网络，没有逐层屏障，也不等待 Host ACK。
两核全部算完之后，Rocket 和 Host 再严格比较全部 120 个张量、保护区及最终 1000 个 INT32 logits。
每张图独立保留其全部 30 个张量，方便验证，不进行激活内存复用。

只读权重、输入、golden 共用同一份 blob；激活空间按任务划分且互不重叠；两个核的栈、日志、状态和通知 cache line 独立。
保持已验证的 per-hart RoCC 配置：两个核使用同一个 custom3 编码，各驱动自己的 Gemmini，不给 hart1 随意换 opcode。

## 2. 计时范围

- 两核完成缓冲区初始化、发布 READY 后，由 hart0 记录起点并 release 发布 GO(run_id)。
- 两核 acquire GO，各自完成被分配的模型任务。模型后端保留已验证的 accelerator 完成 fence 路径，再发布 compute-DONE。
- hart0 等到两核 compute-DONE 后记录整批 makespan，并发布 FINISH。
- 只有 FINISH 之后，两个核才开始逐元素检查。检查和 Host 读回不进入 makespan。
- 每核的本地周期均使用自己的 mcycle 差值，整批时间只使用 hart0 的差值，不相减跨核绝对时间戳。

串行和并行均处理同样四张图、执行同样 120 个步骤，使用相同 ELF 和数据。速度比为：

`speedup = serial_makespan_cycles / dual_makespan_cycles`

计时包含任务调度、进度状态写入和后端 fence 等开销；不含 ELF/输入传输、BSS 清零、激活初始化、数值校验和 Host 读回。
默认是单批首次运行，没有 warm-up 和多轮统计；当前结果用于首版对比，不能声称稳态峰值吞吐或保证 2x。
共享 HBM、L2 和互连可能限制加速比。

## 3. 安装源码

把 `vcu128_resnet18_dual_software.tar.gz` 上传到 `/home/cuixh/`：

```bash
cd /home/cuixh/chipyard
tar -xzf /home/cuixh/vcu128_resnet18_dual_software.tar.gz
```

新源码目录为 `/home/cuixh/chipyard/tests/vcu128_resnet18_ptq/dual`。
不会覆盖已经通过的 `qops`、`full` 目录。本源码包不含交付方的模型数据或预编译 ELF。

## 4. 用已经通过的 full 导出物生成双核图

```bash
source /home/cuixh/.venv/bin/activate
cd /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/dual

python prepare_dual.py \
  --full-generated /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/full/generated \
  --out generated
```

这一步不重新校准、量化或运行 PyTorch。它检查已经通过的 full 数据，再把每张图的输入/输出指针绑定到独立的任务 arena。
原来的权重、INT8 输入和 golden 字节直接复用；同一份权重不会因为双核再复制一套。
当前支持选择 2～16 张、偶数张测试图；默认四张图每核各两张。若要更换选择，在 full 阶段先重新导出并验证对应图片。

四张图预期：

```text
DUAL GRAPH EXPORT PASS: 4 tasks, 120 stages, 10,570,496 private arena bytes; one shared immutable blob
```

## 5. 编译一个双核 ELF

```bash
deactivate
cd /home/cuixh/chipyard
source ./env.sh
hash -r

cd tests/vcu128_resnet18_ptq/dual
make PYTHON=/home/cuixh/.venv/bin/python
```

这次只生成一份 `build/resnet18_dual.elf`。两个核从同一个入口启动，按 mhartid 选择栈和任务；不要分别加载两份单核 ELF。
只有 hart0 清 APP BSS，hart1 在访问任何 APP 可变全局变量前等待 boot(run_id)。
两个核均启用 F/D 和 FS，使用 RV64 IMAFDC、lp64d。Makefile 保留目标标准头文件检查、Gemmini 驱动冻结和 ELF 图指针检查。

可选离线检查：

```bash
make check PYTHON=/home/cuixh/.venv/bin/python
```

有宿主机 GCC 时，可选两线程＋独立 C 标量数值测试：

```bash
make native-check PYTHON=/home/cuixh/.venv/bin/python
```

它执行同一份同步/调度/校验 runtime，分别检查串行、正常并行、交换分配；模型由独立 C 标量实现执行，不执行 Gemmini。
原生时钟是纳秒，不是 FPGA 周期，不将原生性能作为板上速度比。

## 6. 迁移到 Vivado 工程 software

```bash
make host-bundle PYTHON=/home/cuixh/.venv/bin/python

mkdir -p /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet50/software/resnet18_dual

tar -xzf dist/resnet18_dual_host.tar.gz \
  -C /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet50/software/resnet18_dual
```

运行包包含同一份 ELF、绑定的 manifest/blob、Host、报告比较脚本和说明。
继续使用此前完整模型已通过的比特流，本阶段不需要修改 RTL 或重新综合实现。

## 7. 第一轮：真正双核并行

先按此前已经验证的标准流程完成全通路复位，确保本轮一次性 START 未使用。
不向未经验证的软件 RESET 寄存器写入。

```bash
conda deactivate
source /home/cuixh/.venv/bin/activate

cd /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet50/software/resnet18_dual

python host_resnet18_dual.py \
  --elf build/resnet18_dual.elf \
  --manifest generated/dual_manifest.json \
  --mode dual \
  --report reports/dual.json \
  --timeout-sec 1800
```

进度打印可能跳过部分 stage，因为 Host 定时轮询，不再逐层暂停；不影响最终所有张量的严格检查。
每张图结束打印其任务编号、实际执行核和 Top-1。四张图通过时：

```text
PASS RESNET18 DUAL: 4 images, 120 exact chained tensors; makespan=... cycles
```

`reports/dual.json` 保存每张图完整 1000 个 INT32 logits、Top-5、输入 hash、owner、每任务/每核周期、整批 makespan 和验证结果。
如果某核 trap 或数值/保护区检查失败，Host 会给出状态和错误，不会把只完成一核视为通过。

## 8. 第二轮：交换分配

再次执行全通路复位，确保 START 未使用，然后：

```bash
python host_resnet18_dual.py \
  --elf build/resnet18_dual.elf \
  --manifest generated/dual_manifest.json \
  --mode dual --swap \
  --report reports/dual_swap.json \
  --timeout-sec 1800
```

核对输出里同一张图的 hart 编号已经交换，四张图仍全部匹配对应 golden。

## 9. 第三轮：相同工作量串行对照

再次执行全通路复位，再运行：

```bash
python host_resnet18_dual.py \
  --elf build/resnet18_dual.elf \
  --manifest generated/dual_manifest.json \
  --mode serial \
  --report reports/serial.json \
  --timeout-sec 1800
```

串行模式仍启动两个 Rocket 以复用同一套握手；只有 hart0 调用 Gemmini 顺序跑四张图，hart1 等待，不计算模型。
不要拿上一版包含逐层 Host ACK 的 full 程序时间作这里的串行分母。

## 10. 比较结果与速度比

```bash
python compare_runs.py \
  --serial reports/serial.json \
  --dual reports/dual.json \
  --swapped reports/dual_swap.json \
  --out reports/comparison.json
```

脚本先检查三轮 ELF、manifest、量化身份、任务输入 hash、全部 INT32 logits 一致，再计算周期速度比。
请保持三轮使用同一比特流和实际 SoC 频率；报告无法自动证明用户未更换比特流/时钟。

如需换算秒和 images/s，在三轮命令中都加 `--soc-mhz 实际SoC频率`。例如只有实际 SoC 为 50 MHz 时才填 `--soc-mhz 50`；不要填写 HBM 的 450 MHz。
不指定频率也能比较 cycles 和速度比，并报告每百万周期图片数。

## 11. 地址和存储

| 区域 | 地址/大小 |
|---|---|
| APP | `0x80000000`～`0x8f000000`，240 MiB |
| hart0 栈 | `0x8fc00000`～`0x8fd00000` |
| hart1 栈 | `0x8fe00000`～`0x8ff00000` |
| 共享配置、同步行、两核状态 | `0x8ff00000`，4096 B |
| hart0 日志 | `0x8ff10000`，64 KiB |
| hart1 日志 | `0x8ff30000`，64 KiB |
| 激活 arena | APP BSS 内，每任务独立，ELF 符号确定实际地址 |
| 任务结果表 | APP BSS 内，每项 128 B，按 cache line 对齐 |

Host 沿用 CPU/FBus 地址，不自行减 `0x80000000` 转成原始 HBM 偏移。
所有任务 arena 和结果表都由 ELF/manifest 交叉检查边界；输入、权重、偏置和 golden blob 在结束后完整读回，确认未被改写。

## 12. 当前边界

- 这是数据并行：两组完整模型处理不同图片；不将一张图的不同层拆到两核之间。
- 只读权重共享；可变激活不共享。暂不做跨加速器张量流水线。
- 正确性完整检查使用保留全部中间结果的调试内存布局；未来内存复用后应重新验证并重新测量。
- 本地原生两线程验证不是 Gemmini RTL/ISA 仿真或板上验证，实际并行行为、数值和加速比需要上述三轮板测确认。
