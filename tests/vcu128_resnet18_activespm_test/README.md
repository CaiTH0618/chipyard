# VCU128：ActiveSPM 直接访问与覆盖功能测试

完整源码目录，无补丁安装器。不改已经通过的 cache-only 基线。ELF 在 Chipyard 编译，Host 在 Vivado 工程 software 新目录运行。这个阶段使用合成数据，不需要模型权重、full_blob.bin、重新量化或新的 bitstream 构建。

必须下载已生成的 **VCU128DualRocketDualGemminiResNet18ActiveSPMConfig** 对应 bitstream。其配置是 1 MiB 共享 L2＋双 Rocket＋双 Gemmini＋两个各 1 MiB 的 ActiveSPM。当前 FPGA 若仍运行 cache-only bitstream，要先切换。

## 1. 已核对的映射和访问规则

| 实例 | RAM 范围（含末端） | 容量 | 控制基址 |
| --- | --- | --- | --- |
| ActiveSPM0 | 0x70000000–0x700fffff | 1 MiB | 0x10050000 |
| ActiveSPM1 | 0x70100000–0x701fffff | 1 MiB | 0x10051000 |

映射由你提供的生成目录 memmap.json 核对，两个 RAM 均不可缓存、不可执行、不支持原子操作；实例各四 banks、8 字节 beat。`hardware_evidence.json` 保存映射摘录、寄存器偏移及参考源码哈希。

ActiveSPM 的 RAM 可直接访问，不需要写使能寄存器。控制寄存器是其独立 DMA 的描述符与状态，64-bit 对齐访问。此测试**只读控制寄存器，完全不写 ActiveSPM 的 DMA COMMAND/START 或描述符**；前后检查七个寄存器均为复位值零。若旧 DMA 使用留下非零状态，须先完整复位/重新配置 bitstream。

Gemmini 的 mvin/mvout 和算子访存硬件仍然使用；它们与 ActiveSPM 自身 DMA 是两回事。Host 的 XDMA 仍只访问 HBM，不把 0x70000000 当作板外 HBM 地址。Rocket 检查 SPM 数据，最后一项输出另由 Gemmini 搬到 HBM，供 Host 独立检查。

## 2. 测试范围

每个实例每轮共有 **22 个 case**，不是 22 类算子：

| 类别 | case 数 | 主要检查 |
| --- | ---: | --- |
| CPU 全容量地址相关 pattern | 2 | 覆盖全部 1 MiB，首尾、高地址位、bank 别名 |
| CPU 8/16/32/64-bit 读写 | 4 | 对齐子字写入及边界保护 |
| Gemmini HBM→SPM→HBM 往返 | 5 | 小块、7×5 尾块与 stride、31 列尾块、64 字节块/80 字节 stride、最大 200,704 字节张量紧贴 RAM 末端 |
| Matmul | 3 | SPM 输入/输出、9×17 尾块、缩放和 INT32 输出 |
| Conv / fused pooling | 3 | 3×3 卷积、池化融合、7×7 stride2 stem 结构 |
| ResAdd | 4 | signed ties、784×128/196×256/3136×64 实际尺寸、三个独立工作槽及覆盖 |
| GAP | 1 | 实际 7×7×512 规约，SPM 输入/输出 |

所有算子保持当前 8×8 INT8 Gemmini 数值接口。ResAdd 使用已经通过基线复测的显式单累加器 8×8 路径；Conv、Matmul、GAP 数学实现沿用该基线。增加的直接搬运辅助函数与算子共用同一个 Gemmini API 翻译单元。

算子输出写进 SPM 后，在 CPU 比较之前，立即由 Gemmini 读回到 HBM；不依赖先由 CPU 读取来“准备”输入。Rocket 对每次输出逐字节检查，并检查输入保留、保护区及 HBM 镜像。Host 使用独立 NumPy 参考结果检查每个 case 的实际/参考 FNV64；all 模式还会逐字节比较最后一个 case 的完整 HBM 镜像。

每轮、每实例、每个 case 的 pattern 都不同，以检测陈旧 generation。最大残差测试的两个输入和输出各占一个 200,832 字节槽，三个槽共 602,496 字节。这里会由 CPU 准备合成输入并清理输出，用于功能检查；不是完整模型的性能程序。

hart0 在两种单实例模式下都会先做两块 SPM 的少量高低地址/实例别名探针；随后只有选中的 hart 执行完整 case。`hart1` 模式由 hart1/Gemmini1/SPM1 执行，hart0 协调启动和屏障。`dual` 模式两核并发访问各自 SPM，pattern 不同。

## 3. 放置完整目录并编译（Chipyard）

将 `vcu128_resnet18_activespm_test.tar.gz` 放到 `/home/cuixh/chipyard/tests/`：

```bash
cd /home/cuixh/chipyard/tests
tar -xzf vcu128_resnet18_activespm_test.tar.gz

source /home/cuixh/chipyard/env.sh
cd /home/cuixh/chipyard/tests/vcu128_resnet18_activespm_test
/home/cuixh/.venv/bin/python -m pip install -r requirements.txt

/home/cuixh/.venv/bin/python prepare_test.py --out generated --max-iterations 4
make PYTHON=/home/cuixh/.venv/bin/python
make host-bundle PYTHON=/home/cuixh/.venv/bin/python
```

这是一个新的并列目录；不替换 `vcu128_resnet18_cache_baseline`。生成数据是测试参数与独立参考哈希，不是量化模型导出。准备时允许 max-iterations=1–32，默认 4；Host 的 iterations 不能超过本次准备值。改变源码或最大轮数后重新 prepare、make、host-bundle。

同一份 `build/activespm_test.elf` 支持 hart0/hart1/dual，不用分别 make HART=0/1。编译仍核对冻结 Gemmini API 和 8×8 参数，并检查 ELF/manifest/测试表绑定。若 target stdio.h/stdlib.h 缺失，使用此前能编译基线的 Chipyard 工具链，不要用宿主机头文件代替。

## 4. 部署 Host 到 Vivado 工程

仍在上述 Chipyard 测试目录执行：

```bash
mkdir -p /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_activespm_test

tar -xzf dist/activespm_test_host.tar.gz \
  -C /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_activespm_test

source /home/cuixh/.venv/bin/activate
cd /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_activespm_test
python host_activespm_test.py --dry-run
```

如两个环境不在同一台机器，传输 `dist/activespm_test_host.tar.gz` 到测试机器再解压。dry-run 不访问设备。Host 的依赖是 numpy/pyelftools，使用同一个 .venv 即可。

## 5. 切换 ActiveSPM bitstream

使用之前成功的 Hardware Manager/PCIe/XDMA 流程，下载 **resnet18_activespm** 对应 bitstream，并恢复驱动/设备。不要使用 cache-only 镜像跑本测试。

控制 ID 仍为 0x534f4301，READY 在 BAR 状态 0x08 bit1，START 写 0x0c。**每个独立 Host 命令前都恢复完整路径的单次启动状态**：按已验证方法复位或重新配置 bitstream 并恢复 XDMA。本程序不写不存在的 0x10 reset 寄存器。一次 Host 调用中的四轮由目标屏障协调，不需要中途重配。

`--bitstream /实际/activespm.bit` 可把本地 bit 文件哈希记入报告，不会下载或自动识别 FPGA 镜像。

## 6. 第一步：hart0 CPU RAM 检查

```bash
python host_activespm_test.py \
  --elf build/activespm_test.elf \
  --mode hart0 --suite cpu --iterations 1 \
  --timeout-sec 1800 --report reports/activespm_hart0_cpu.json
```

预期 `PASS ACTIVESPM HART0 CPU: 6 checked cases; ActiveSPM DMA untouched`，额外的双实例地址探针也通过。该模式不发 Gemmini 命令，用于先确认 RAM、控制访问和启动正常。

## 7. 第二步：hart0 完整功能与四轮覆盖

先恢复单次启动状态，再执行：

```bash
python host_activespm_test.py \
  --elf build/activespm_test.elf \
  --mode hart0 --suite all --iterations 4 \
  --timeout-sec 1800 --report reports/activespm_hart0_all.json
```

预期 `PASS ACTIVESPM HART0 ALL: 88 checked cases; ActiveSPM DMA untouched`。

## 8. 第三、四步：hart1 和双实例并发

每条命令之间恢复单次启动状态：

```bash
python host_activespm_test.py \
  --elf build/activespm_test.elf \
  --mode hart1 --suite all --iterations 4 \
  --timeout-sec 1800 --report reports/activespm_hart1_all.json
```

预期 88 checked cases。

```bash
python host_activespm_test.py \
  --elf build/activespm_test.elf \
  --mode dual --suite all --iterations 4 \
  --timeout-sec 1800 --report reports/activespm_dual_all.json
```

预期 176 checked cases。并发覆盖两核、两 Gemmini、两 ActiveSPM，无共享输入/输出工作槽。

## 9. 报告和错误解释

成功报告包含各 case、每轮哈希和周期、双 hart 状态、两个实例控制寄存器前后快照、地址探针结果、最终完整镜像、源文件/ELF/manifest 哈希。`eligible_for_performance_comparison=false`：CPU 参考、输入生成、检查、保护区和镜像都在诊断流程内，周期不可用于宣称推理加速。

错误码：1 配置，2 目标等待超时，3 对端失败，4 数据差异，5 保护区，7 CPU trap，8 ActiveSPM 控制非复位/非空闲，9 地址/实例别名。Host 遇到已发布的 ERROR 会保存失败 JSON，显示 hart、iteration（task_index 从 0 计）、case（stage 从 1 计）、mcause/mepc/mtval 和首差异。CPU trap/早期启动时 case=0 表示启动或控制/别名探针。

若失败，保留控制台输出和 JSON，不立即覆盖当前内存现场。CPU 若在单条访存或 Gemmini fence 内停住，软件无法自行抢占该操作；Host timeout 用于保留最后进度并提示排查。

## 10. 交付前验证和边界

已完成真实 RV64 lp64d ELF 编译、链接、dry-run 绑定检查，及 11 项主机回归：hart0/hart1 CPU、各自完整四轮、双实例四轮、别名/控制/输出/保护区/搬运错误注入、配置拒绝。主机 harness 将 RAM/RoCC 操作替换成模型，不是 RTL 仿真；它验证执行、地址、参考数值与协议，不能证明板卡访存硬件正确。没有在此运行你的 FPGA，以上 PASS 是本地验证，板上仍需按顺序执行。

可在 make 之后运行 `make check PYTHON=/home/cuixh/.venv/bin/python`，需要主机 gcc。测试会使用 build/inputs 的冻结参数，不发板卡请求。

本源码压缩包不含预编译 ELF、build/generated、模型参数或补丁脚本。完整模型迁入 ActiveSPM 是下一阶段：通过这些微测试后，再保持 baseline 的 DAG/数值契约，把每 hart 的三个中间工作槽解析到对应 SPM。
