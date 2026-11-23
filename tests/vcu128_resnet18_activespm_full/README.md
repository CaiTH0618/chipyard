# VCU128 ResNet18：完整模型 ActiveSPM 正确性与性能

这是一个独立、已整合的完整源码目录，不需要补丁安装器。不修改已通过的 cache-only 基线或 ActiveSPM 功能测试目录。ELF 仍在 Chipyard 环境编译，Host 部署到 Vivado 工程的 software/resnet18_activespm_full。

## 1. 计算路径与实验分组

同一份 ELF 在运行时选择中间张量的存放位置：

| 组别 | 下载的硬件 | Host 参数 | 中间张量 |
| --- | --- | --- | --- |
| B0 | 2 MiB cache-only bitstream | --hardware cacheonly --storage hbm | HBM 三槽，cache 自动管理 |
| B1 | 1 MiB L2＋双 1 MiB ActiveSPM bitstream | --hardware activespm --storage hbm | HBM 三槽；SPM RAM 不使用 |
| B2 | 与 B1 相同的 ActiveSPM bitstream | --hardware activespm --storage spm | 实际执行 hart 对应 SPM 中的三槽 |

默认 B2、mode=serial：hart0 处理四张图，hart1 不执行模型，SPM1 不被初始化或访问 RAM。可选 mode=hart1 由 hart1/SPM1 处理全部任务；mode=dual 按 task_index 奇偶分工，--swap 交换任务。SPM 基址始终按实际 hart 解析，不按任务索引解析。

每个 hart 三个槽含保护区共 602,496 B，沿用同一份 DAG/最后使用位置分配，不发生活跃张量覆盖。SPM0=0x70000000，SPM1=0x70100000，各 1 MiB。权重、bias、图片输入和 golden 在 HBM；每图最终 1000 个 INT32 logits 也在 HBM。

Conv、Matmul、GAP、ResAdd 代码与当前已通过的 cache-only 整合版相同，model_backend.c 和 resadd_direct.h 的哈希应一致。ResAdd 使用 direct_accumulator_8x8_v1。后继层由 Gemmini 直接读取 SPM 中的前层实际输出；PERF 期间不插入 CPU 张量复制、快照、逐层清零或校验。不使用 ActiveSPM 自身 DMA。

ActiveSPM 硬件上只读两个实例控制寄存器，启动前和计算结束后确认状态/描述符仍为复位值零。PERF 的这些检查和工作槽保护区检查都在计时范围外。Host XDMA 只访问 HBM，不直接读 SoC 内部 SPM。

## 2. 容量和性能口径

单组主比较 B0 对 B2：2 MiB L2 对 1 MiB L2＋一个参与运行的 1 MiB SPM，总参与容量同为 2 MiB。硬件中另一个 SPM 的物理容量仍在资源表里如实记录，但不作为本次单组可用容量池。B1 对 B2 是同一硬件上的放置消融，B0 对 B1 用于观察缩小 L2 的影响。

当前 dual 模式下 B2 的参与容量为 1 MiB＋两个 1 MiB SPM=3 MiB，当前 B0 仅 2 MiB，因此不能把这两个双组结果宣称为等容量实验。比较脚本会拒绝这一容量不匹配。

计时沿用基线：每轮 hart0 发布 GO 前开始，至确认两个 hart 的计算 DONE 为止。包含调度、地址解析、Gemmini fence、必要同步和 Host 轮询干扰；排除模型加载、warmup、最终 logits 校验、控制寄存器/保护区检查及 Host 输出回读。

DEBUG 每层输出完成后、覆盖前逐字节检查。warmup=1/repeats=5 时共 720 个阶段输出检查，DEBUG 周期不作性能数据。PERF 仅在计算全部结束后检查最终输出；重复轮次覆盖各图的同一最终输出槽，所以精确核对的是最后一轮四张图的完整 logits，不保留全部 20 张计时图像的历史输出。

两边保持相同输入顺序、量化数据、算子、槽计划、warmup/repeats、轮询间隔和实际 SoC 频率。默认 PERF poll-sec=1.0，DEBUG=0.1。--soc-mhz 仅用于换算，不改变 FPGA 频率。这是指定 warmup 后的测量，不是严格冷 cache 测试。尚未采集 HBM 流量计数器，不能仅凭周期下降声称 HBM 流量下降。

## 3. 在 Chipyard 放置并编译

把 vcu128_resnet18_activespm_full.tar.gz 放到 /home/cuixh/chipyard/tests：

```bash
cd /home/cuixh/chipyard/tests
tar -xzf vcu128_resnet18_activespm_full.tar.gz
source /home/cuixh/chipyard/env.sh
cd /home/cuixh/chipyard/tests/vcu128_resnet18_activespm_full
/home/cuixh/.venv/bin/python -m pip install -r requirements.txt

/home/cuixh/.venv/bin/python prepare_full_spm.py \
  --full-generated /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/full/generated \
  --out generated
make PYTHON=/home/cuixh/.venv/bin/python
make host-bundle PYTHON=/home/cuixh/.venv/bin/python
```

继续使用已验证的 full_manifest.json/full_blob.bin，不重新量化。内部生成文件保留 baseline_manifest.json/baseline_blob.bin 等名称，但使用新的 ABI 3 和 magic，不能与旧基线 ELF/Host 混用。导出同时记录 runtime、算子、ResAdd、模型和布局哈希；修改源码后重新 prepare/make/host-bundle。

得到 build/resnet18_activespm_full.elf 和 dist/resnet18_activespm_full_host.tar.gz。相同 ELF 支持 B0/B1/B2 及所有 hart 模式，不用分别 make HART=0/1。

## 4. 部署 Host

仍在 Chipyard 新测试目录中执行：

```bash
mkdir -p /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_activespm_full
tar -xzf dist/resnet18_activespm_full_host.tar.gz \
  -C /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_activespm_full

source /home/cuixh/.venv/bin/activate
cd /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_activespm_full
python host_resnet18_activespm_full.py --dry-run
```

如果两个环境在不同机器，传输这个 Host tar 包再解压。报告保留在新 software 目录，不覆盖现有基线报告。

## 5. 先验证完整网络 B2 DEBUG

使用已通过功能测试的 ActiveSPM bitstream，按原流程完整复位或重新配置并恢复 XDMA，保证 START 尚未使用：

```bash
python host_resnet18_activespm_full.py \
  --hardware activespm --storage spm --mode serial \
  --check debug --warmup 1 --repeats 5 \
  --timeout-sec 1800 --report reports/B2_spm_serial_debug.json
```

目标为 PASS RESNET18 FULL SPM DEBUG SERIAL；checked stages=720，四图完整 logits 一致。报告 hart0 work_base=1879048192（0x70000000），storage=1、work_guard_ok=1；hart1 未执行模型，work_guard_ok=0。控制快照全部为零。不要把前面的合成算子功能通过当作这一步已经完成。

## 6. B2：单组 ActiveSPM 性能

**每个独立 Host 命令前都恢复完整路径的单次启动状态。** 不在程序运行中重新加载 ELF，也不写不存在的 0x10 reset。无需重新生成 RTL/bitstream，只需下载对应的已有镜像。

DEBUG 通过后，保持相同 ActiveSPM bitstream，完整复位/重新配置并恢复 XDMA，再执行：

```bash
python host_resnet18_activespm_full.py \
  --hardware activespm --storage spm --mode serial \
  --check perf --warmup 1 --repeats 5 --soc-mhz 50 \
  --timeout-sec 1800 --report reports/B2_spm_serial_perf.json
```

预热四张图，再计时五轮共 20 张图，最后检查四份 logits。报告包括每轮 cycles、总周期、吞吐、预测、容量口径和版本哈希。

## 7. B1：同一硬件上使用 HBM 的消融

仍用 ActiveSPM bitstream。先恢复启动状态，建议先 debug，再恢复并 perf：

```bash
python host_resnet18_activespm_full.py \
  --hardware activespm --storage hbm --mode serial \
  --check debug --warmup 1 --repeats 5 \
  --timeout-sec 1800 --report reports/B1_hbm_serial_debug.json
```

```bash
python host_resnet18_activespm_full.py \
  --hardware activespm --storage hbm --mode serial \
  --check perf --warmup 1 --repeats 5 --soc-mhz 50 \
  --timeout-sec 1800 --report reports/B1_hbm_serial_perf.json
```

## 8. 用同一 ELF 补齐 B0，进行正式比较

已有旧版 B0 报告仍是有效历史基线。新程序增加了存储选择与诊断字段，runtime/ABI 与旧版不同；为了正式比较时两边连运行时也一致，换回已生成的 2 MiB cache-only bitstream，用本次同一份 ELF 补跑 B0。不是重新构建硬件。

```bash
python host_resnet18_activespm_full.py \
  --hardware cacheonly --storage hbm --mode serial \
  --check debug --warmup 1 --repeats 5 \
  --timeout-sec 1800 --report reports/B0_cacheonly_debug.json
```

再次恢复 cache-only 的单次启动状态后：

```bash
python host_resnet18_activespm_full.py \
  --hardware cacheonly --storage hbm --mode serial \
  --check perf --warmup 1 --repeats 5 --soc-mhz 50 \
  --timeout-sec 1800 --report reports/B0_cacheonly_perf.json
```

cache-only 模式不访问 ActiveSPM 控制或 RAM 地址。--hardware 和容量标签都是操作者声明，不会自动识别 bitstream；应在 Hardware Manager 确认下载了正确镜像。可为每条命令加 --bitstream /实际路径/对应.bit 记录哈希（不下载、不改频）。

## 9. 自动比较

在新 Host 目录执行：

```bash
python compare_performance.py \
  --b0 reports/B0_cacheonly_perf.json \
  --b1 reports/B1_hbm_serial_perf.json \
  --b2 reports/B2_spm_serial_perf.json \
  --out reports/performance_comparison.json
```

--b1 可省略。脚本核对完整 logits、任务归属、输入、量化/模型、ELF/manifest/runtime/算子/布局、频率、预热/轮次及轮询设置；拒绝 DEBUG、旧 ABI、不匹配和非等容量结果。输出 B2/B0 加速比、延迟变化及 B1/B2 消融。比值可能小于 1，应按测量结果解释；不能预设 ActiveSPM 一定加速。

## 10. 可选 hart1 / dual / swap

前面单组主比较通过后，可切回 ActiveSPM bitstream，按相同启动恢复规则运行 --mode hart1（全部任务交 hart1），或 --mode dual，或 --mode dual --swap。先 debug，再 perf。

示例：python host_resnet18_activespm_full.py --hardware activespm --storage spm --mode dual --swap --check debug --warmup 1 --repeats 5 --report reports/B2_dual_swap_debug.json

这些双组运行用于功能与吞吐扩展；当前硬件容量与 2 MiB cache-only 双组不等，正式比较脚本会拒绝等容量结论。

## 11. 验证边界

交付前完成主机合成数据的协议/生存期/绑定检查：HBM/SPM、hart0/hart1、dual/swap、warmup/repeats、空闲 SPM 不被写入、控制/数值/保护区错误拒绝，以及比较脚本的设置/容量/全 logits 拒绝逻辑。已用合成数据完成实际 RV64 lp64d ELF 编译、链接与 dry-run；算子文件和已通过基线的哈希一致。

本地没有你的真实 full_blob.bin，未在此运行真实模型或板卡；因此必须完成上述 B2 DEBUG 和 PERF。合成数据和编译测试 ELF 不随源码包交付，Host 会拒绝把标记为 test_fixture_only 的数据运行到板上。

可选离线回归：make check PYTHON=/home/cuixh/.venv/bin/python，需要主机 gcc、pyelftools；make check-resadd 还使用冻结 Gemmini 参数做指令模型测试。它们不能替代 FPGA 验证。
