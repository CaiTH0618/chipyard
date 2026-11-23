# VCU128 ResNet18：复用 HBM 地址的 cache-only 基线

这是已整合 ResAdd 修改的完整源码目录，直接 prepare、make、host-bundle 即可，不需要补丁安装器。所有残差加法使用 Gemmini 单个 8×8 累加器块，依次覆盖搬入 A、累加搬入 B、搬出结果，每步保留完成 fence。绕过 loop-resadd，未改为 CPU 计算。此路径作为修复候选，板上尚需通过 debug warmup=1/repeats=5 和相同设置的 perf。性能可能降低；后续 ActiveSPM 版本必须使用相同后端进行比较。继续使用现有 cache-only bitstream。

这是一个独立测试目录。ELF 在 Chipyard 下生成，Host 程序在 Vivado 工程的 software 新目录中运行。现有 qops/full/dual 目录不需要修改。

本程序面向已经生成的 **2 MiB 共享 L2、双 Rocket＋双 Gemmini、无 ActiveSPM** bitstream。默认先测单组：hart0 连续处理四张图，hart1 只参与启动和同步、不执行模型。L2 是否命中及替换仍由硬件决定；软件只安排 HBM 数组的生存期和地址覆盖。

## 1. 实现内容

- 从原 full 导出的 DAG 分析消费者和最后使用位置，按确定性规则分配三个工作槽。算子输出分配在释放输入之前，不假设算子支持原地覆盖。
- 每个 hart 三个槽，最大有效载荷各 200,704 字节；含保护区共 **602,496 字节/hart**。两个 hart 的工作空间共 1,204,992 字节。
- 残差/投影需要的输入保留到最后一个消费者完成；槽空闲后才能覆盖。图像之间也复用三个槽。
- 每个 task 另存最终 1000 个 INT32 logits，四张图各一个最终输出槽，含保护区共 16,640 字节。
- 卷积、GAP 和 FC 沿用原后端；ResAdd 改为显式累加器分块候选路径并保留完成 fence。每层实际计算始终读取前层的实时输出，不读取 golden 代替计算输入。
- 不使用 ActiveSPM，也不调用 ActiveSPM DMA。这份软件用于建立后续 ActiveSPM 比较的 cache-only 基线。

| 模式 | 中间结果 | 校验 | 可作性能数据 |
| --- | --- | --- | --- |
| `--check debug` | 三槽复用 | 每层输出完成后、覆盖前逐字节核对，另查保护区；最终 logits 再由 Host 核对 | 否 |
| `--check perf` | 相同三槽复用 | 全部计时结束后核对最终 logits 和外侧保护区 | 是，记录 warmup/时钟/轮次等条件 |

四张图、每图 30 个计算阶段，debug 默认共检查 **120 个阶段输出**。这里不是 120 类算子。

## 2. 放置完整源码：Chipyard 环境

把 `vcu128_resnet18_cache_baseline_complete.tar.gz` 放到 `/home/cuixh/chipyard/tests/`。将现有目录整体改名保留，再解压出一份干净的同名目录：

```bash
cd /home/cuixh/chipyard/tests
mv vcu128_resnet18_cache_baseline vcu128_resnet18_cache_baseline_previous_$(date +%Y%m%d_%H%M%S)
tar -xzf vcu128_resnet18_cache_baseline_complete.tar.gz
source /home/cuixh/chipyard/env.sh
cd /home/cuixh/chipyard/tests/vcu128_resnet18_cache_baseline
/home/cuixh/.venv/bin/python -m pip install -r requirements.txt
```

若本来没有旧目录，省略 mv。解压后的目录应为 `/home/cuixh/chipyard/tests/vcu128_resnet18_cache_baseline/`，与 `vcu128_resnet18_ptq/` 并列。此目录中都是整合后的源码，没有补丁脚本、旧版本副本或旧 build/generated。旧目录仅作为整体回退副本，不参与新程序构建。

## 3. 从已经通过的 full 导出生成基线数据

```bash
/home/cuixh/.venv/bin/python prepare_baseline.py \
  --full-generated /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/full/generated \
  --out generated
```

输入目录必须同时有 `full_manifest.json` 和 `full_blob.bin`，使用你已经通过单/双 hart 测试的同一份数据。不重新校准或量化，不重新执行旧 full 导出程序。

脚本检查原数据哈希和图依赖，生成 `baseline_manifest.json`、`baseline_blob.bin`、C 图描述和链接用汇编。manifest 包含逐阶段槽分配及最后使用位置。`test_fixture.json` 只是离线测试元数据，不能替代真实量化数据。

## 4. 编译同一份双 hart ELF

```bash
make PYTHON=/home/cuixh/.venv/bin/python
```

得到 `build/resnet18_cache_baseline.elf`。**不用分别 make HART=0/1**：同一 ELF 按 mhartid 分工，Host 选择 serial/dual。

编译包含 Gemmini 8×8 INT8 头文件快照检查和 ELF/manifest/blob/槽引用的一致性检查。不要跳过 `freeze_headers.py` 的检查。

若又出现找不到 `stdio.h`/`stdlib.h`，先确认已经 source Chipyard 的 `env.sh`，查看 `command -v riscv64-unknown-elf-gcc`。之前日志里的 `/usr/bin` 无 newlib 工具链不能直接用于本程序；应使用你原先能够编译通过的 Chipyard 工具链。可用 `make CROSS=/你的工具链/bin/riscv64-unknown-elf- PYTHON=...` 显式指定，不要加入宿主机 `/usr/include`。

## 5. 打包并部署 Host：Vivado 工程环境

仍在 Chipyard 新测试目录执行：

```bash
make host-bundle PYTHON=/home/cuixh/.venv/bin/python
mkdir -p /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_cache_baseline
tar -xzf dist/resnet18_cache_baseline_host.tar.gz \
  -C /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_cache_baseline
```

若两个环境在不同机器，先传输这个 Host 压缩包，再在目标机器执行 mkdir/tar；不在 Vivado 目录编译 ELF。

然后切换到新 Host 目录：

```bash
source /home/cuixh/.venv/bin/activate
cd /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_cache_baseline
python -m pip install -r requirements.txt
python host_resnet18_cache_baseline.py \
  --elf build/resnet18_cache_baseline.elf --dry-run
```

dry-run 不访问设备，先检查新 ELF 与新 manifest 是否配套。每次重新导出/编译后重新打包并整体部署，避免只替换一个文件。

## 6. 第一轮：cache-only bitstream 上的单组重复正确性

用已验证的流程下载 **resnet18_cacheonly** bitstream，恢复 PCIe/XDMA，使板卡处于尚未触发 START 的状态，再执行：

```bash
python host_resnet18_cache_baseline.py \
  --elf build/resnet18_cache_baseline.elf \
  --mode serial --check debug --warmup 1 --repeats 5 \
  --timeout-sec 1800 --report reports/cacheonly_direct_resadd_debug_repeat5.json
```

目标结果：

- Address probe PASS；两 hart 启动正常。
- hart0 预热 1 轮并测量 5 轮，六轮 × 四张图 × 30 层 = 720 个逐层精确检查；hart1 不执行模型。
- 四个 TOP1，完整 1000 INT32 logits/task 与已导出 golden 精确一致。
- 工作槽、最终输出、外侧保护区及不可变模型数据回读通过。
- 最终 `PASS CACHE BASELINE DEBUG SERIAL`，报告 `debug_checked_stage_count=720`。

Host 保留之前的地址探针、ELF 回读和单次 START 路径。遇到错误会输出 task/stage、错误码，以及可用时的首差异位置/字节。`mismatches` 为首次失败标记，不表示扫描统计了全部差异。

**一个 Host 命令结束后，下一次命令前要按你已经验证的方法执行完整路径复位或重新配置 bitstream，并恢复 XDMA。** 本程序不发明新的 BAR reset：只使用控制 ID、状态和 `0x0c` START。START 已使用时会拒绝再次加载，以免覆盖仍在运行/停驻的旧程序。

## 7. 第二轮：单组性能基线

debug 通过后，先复位/重新配置同一份 cache-only bitstream，然后执行：

```bash
python host_resnet18_cache_baseline.py \
  --elf build/resnet18_cache_baseline.elf \
  --mode serial --check perf --warmup 1 --repeats 5 \
  --soc-mhz 50 --timeout-sec 1800 \
  --report reports/cacheonly_direct_resadd_perf_repeat5.json
```

一次 START 内先完整处理四张图作为 1 轮 warmup，再测量 5 轮，共 20 张计时图像。warmup 和模型加载不计入性能周期。可把 repeats 设为 1–32，warmup 设为 0–32。

每轮计时从 hart0 发布 GO 前开始，至确认两个 hart 完成计算为止，包含任务调度、指针解析、Gemmini fence 与必要同步；不包含最后的数值比较和 Host 结果传输。PERF 不逐层清零、填毒、保存快照或比较 golden。

每图的最终输出槽会在重复轮次间覆盖，因此 **PERF 精确核对的是最后一轮的四份完整 logits**，不是保留了 20 张历史输出。DEBUG 对每轮（包含 warmup）的每层都检查。

报告包含每轮 cycles、总 makespan、median batch cycles、吞吐、每任务算子周期、完整 logits、Top5、ELF/manifest 哈希及模式。`--soc-mhz` 是你提供的实际时钟，仅用于周期换算，并不改频；cycles 是主要原始数据。

这不是严格冷 cache 测试：ELF 装载及启动已经发生，之后进行指定 warmup。比较时两边保持相同 warmup/repeats、轮询间隔、输入顺序、频率与算子后端。默认 PERF Host 每秒轮询，轮询干扰仍存在，不应当作完全无观测开销的测量。

`--bitstream /实际保存的/cacheonly.bit` 可将该文件 SHA256 加入报告；这不会下载或自动识别板上配置，`--hardware-label` 也仅是人工标签。必须自己确认 Hardware Manager 下载的是正确 bitstream。

## 8. 可选：双组与交换任务回归

先完成上面的单组。每条命令之间都恢复板卡单次启动状态：

```bash
python host_resnet18_cache_baseline.py \
  --elf build/resnet18_cache_baseline.elf \
  --mode dual --check debug --report reports/cacheonly_dual_debug.json
```

```bash
python host_resnet18_cache_baseline.py \
  --elf build/resnet18_cache_baseline.elf \
  --mode dual --swap --check debug --report reports/cacheonly_dual_swap_debug.json
```

默认 dual：任务索引 0/2 给 hart0，1/3 给 hart1；swap 后交换。每个 hart 只有自己的三个工作槽，没有交叉复用。两个命令都应共检查 120 个阶段输出。

dual 性能可将 check 换为 perf，并指定相同 warmup/repeats。当前 2 MiB cache-only 和“1 MiB L2＋两个 1 MiB ActiveSPM”只在**单活跃组按 L+S=2 MiB**的口径下等容量；双组同时使用两块 SPM 时总容量 3 MiB，不能直接宣称与 2 MiB cache-only 等容量。

## 9. 离线验证与当前边界

在源码目录可执行 `make check PYTHON=/home/cuixh/.venv/bin/python`，需要主机 gcc 和 pyelftools。它使用真实 120-stage 图的尺寸/依赖、合成数据和主机线程，验证生存期、槽复用、单/双组同步、swap、warmup/repeats、错误传播、保护区、ELF 元数据绑定和部署打包。离线测试不验证 FPGA 数值计算，也不将合成 ELF/数据发布为板上程序。

如需额外的真实导出数据 CPU 数值检查，生成数据后可执行 `make native-numeric-check PYTHON=...`。该目标使用主机 scalar 参考算子和实际量化参数，不驱动 FPGA；耗时可能很长，不是上板前的必经步骤。

交付前已完成主机离线回归。ResAdd 修复候选另完成 64 组主机指令模型检查，并用合成数据完成 RV64 lp64d ELF 编译、链接和 dry-run 绑定检查。交付环境没有你的完整 `full_blob.bin`，**尚未执行真实模型/板上检查**。合成测试不能证明 FPGA 数值正确，请以本地 make、DEBUG_EXACT 和 PERF 结果为最终依据。源码包不包含模型权重和预编译 ELF。

## 10. 文件与地址约定

`prepare_baseline.py` / `memory_plan.py`：导出与生存期分配；`runtime.c`：执行/同步/校验；`model_backend.c`：Gemmini 算子；`bundle.py`：ELF/manifest 绑定与结果检查；`host_resnet18_cache_baseline.py`：XDMA Host；`package_host.py`：配套部署包。

ELF 从 `0x80000000` 启动，APP 上界 `0x8f000000`；保留原 stack、shared/status、log 的 HBM 分区。启动、陷阱和浮点初始化沿用已通过的代码。新 ABI 和 magic 防止误用旧 Host/ELF。所有工作槽都在 HBM 的 ELF BSS 中，其物理地址由链接结果决定；这并不锁定 cache 行驻留。

后续 ActiveSPM 版本需验证 Gemmini 可直接读写 ActiveSPM 地址空间后，再复用相同 DAG/槽生存期安排，替换工作槽的地址来源。这一包不预先假定该通路已经验证。
