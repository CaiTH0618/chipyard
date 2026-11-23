# ResNet18：激活三槽＋部分权重常驻 ActiveSPM

本包是完整源码目录，不是补丁。Chipyard 侧生成 ELF，再把 Host 包放到 Vivado 工程的软件目录运行。继续使用当前已验证的 ActiveSPM bitstream，不改硬件，不重新量化。

## 本轮比较

同一个 ELF 通过运行参数切换：

| 选项 | 激活 | 权重 | 作用 |
|---|---|---|---|
| `--storage spm --weights none` | 原来三个 SPM 槽 | 全部 HBM | 新运行时中的原 B2 策略 |
| `--storage spm --weights resident` | 相同三个 SPM 槽 | 部分常驻 SPM，其余 HBM | 新策略，以下称 B3 |
| `--storage hbm --weights none --hardware cacheonly` | 三个 HBM 槽 | 全部 HBM | 后续补测同 ELF 的 B0 |

本轮先比较 B2/B3，使用 `--mode serial`：hart0 跑图像，hart1 不执行模型、不访问 SPM 存储区。ABI 从 3 升为 4，新增初始化和逐层计时；旧报告保留为历史结果，不能与新报告直接自动混比。`--hardware` 是人工硬件声明，不识别或下载 bitstream。

## 默认布局

每个参与执行的 hart 使用对应的 1 MiB SPM：hart0 为 `0x70000000`，hart1 为 `0x70100000`。

| 内容 | 字节数 |
|---|---:|
| 原有三个激活槽，含保护区 | 602,496 |
| 常驻完整 INT8 权重载荷 | 419,008 |
| 权重对齐和保护区 | 1,152 |
| 总占用 | 1,022,656，约 97.53% |
| 剩余 | 25,920 |

权重放在激活区之后，载荷 64 B 对齐，每组前后各有 64 B 保护区。默认 `early` 按执行顺序选择能放下的完整数组，是候选策略，并非已测最优方案。

| 节点 | 权重大小/B |
|---|---:|
| stem.pool | 9,408 |
| layer1.0.conv1_relu | 36,864 |
| layer1.0.main_preadd | 36,864 |
| layer1.1.conv1_relu | 36,864 |
| layer1.1.main_preadd | 36,864 |
| layer2.0.conv1_relu | 73,728 |
| layer2.0.main_preadd | 147,456 |
| layer2.0.skip_projection | 8,192 |
| layer3.0.skip_projection | 32,768 |

原始权重布局、数值、bias、scale 和 Gemmini 算子后端保持一致，只替换选中算子的权重指针。四图共享同一份常驻权重，不为每图复制一份。

Rocket 在所有轮次之前用 64-bit 访问复制 HBM 权重到 SPM，并逐字节核对。所有 warmup 和 measured 轮次使用此副本；完成后再核对权重和保护区。不启动 ActiveSPM 自带 DMA，Host 不直接访问 SPM。

## 1. 在 Chipyard 解压、生成数据、编译

将源码包下载到 `/home/cuixh/Downloads` 后：

```bash
cd /home/cuixh/chipyard
tar -xzf /home/cuixh/Downloads/vcu128_resnet18_activespm_weights.tar.gz -C tests
source env.sh
cd tests/vcu128_resnet18_activespm_weights

/home/cuixh/.venv/bin/python prepare_weights.py \
  --full-generated /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/full/generated \
  --weight-profile early --out generated

make PYTHON=/home/cuixh/.venv/bin/python
make host-bundle PYTHON=/home/cuixh/.venv/bin/python
```

`--full-generated` 指向此前验证通过的 `full_manifest.json` 与 `full_blob.bin` 所在目录。它们包含已有模型参数、输入和参考结果，不需要重新校准或量化。源码包不附带模型数据或预编译 ELF，避免误用合成数据上板。

生成阶段打印选中数组、载荷大小和占用信息，详细地址在 `generated/weight_plan.json`。编译会校验已验证的 Gemmini 头文件版本、源码/manifest 绑定，并运行 ELF dry-run。

产物：

- `build/resnet18_activespm_weights.elf`
- `generated/baseline_manifest.json`、`baseline_blob.bin`、`weight_plan.json`
- `dist/resnet18_activespm_weights_host.tar.gz`

如果出现 `stdio.h` 缺失，先检查 `source env.sh` 后实际使用的交叉工具链；不要使用宿主机头文件补交叉编译环境。

## 2. 解压 Host 包到 Vivado 工程

```bash
mkdir -p /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_activespm_weights

tar -xzf /home/cuixh/chipyard/tests/vcu128_resnet18_activespm_weights/dist/resnet18_activespm_weights_host.tar.gz \
  -C /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_activespm_weights

source /home/cuixh/.venv/bin/activate
cd /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet18_activespm/software/resnet18_activespm_weights
```

Host 依赖 pyelftools，旧程序若能运行，通常已有；需要时执行 `python -m pip install -r requirements.txt`。

**以下每次上板命令之间，执行你此前验证成功的完整复位／重下载流程。START 是一次性的。** 使用现有 ActiveSPM bitstream，不需要重新综合实现；`--soc-mhz` 应填写实际 SoC 频率，以下为 50 MHz。

## 3. 先检查正确性

先验证新 ELF 的原 B2 路径：

```bash
python host_resnet18_activespm_weights.py \
  --hardware activespm --storage spm --weights none \
  --mode serial --check debug --warmup 0 --repeats 1 \
  --soc-mhz 50 --timeout-sec 1800 \
  --report reports/B2_weights_none_debug.json
```

复位／重下载后，验证权重常驻和重复执行：

```bash
python host_resnet18_activespm_weights.py \
  --hardware activespm --storage spm --weights resident \
  --mode serial --check debug --warmup 1 --repeats 5 \
  --soc-mhz 50 --timeout-sec 1800 \
  --report reports/B3_weights_resident_debug.json
```

四图时第二条命令应逐字节核对 `(1+5)×4×30=720` 个 stage，最终四份 1000 个 INT32 logits 完全一致；权重初始和结束校验通过，保护区完整，ActiveSPM DMA 控制状态保持空闲。

DEBUG 时间包含逐层校验，不能作性能数据。失败报告包含目标 hart 和权重检查状态。`error=9` 表示权重数据不一致，`error=10` 表示权重保护区损坏；`resident_weight_statuses` 的 `first_bad_entry` 对照 `weight_plan.json` 可定位数组，`first_bad_byte` 定位字节。

## 4. 用相同 ELF 测量持续运行性能

每条命令之前完成复位／重下载。两边保持相同 warmup、repeats、频率和轮询间隔。

```bash
python host_resnet18_activespm_weights.py \
  --hardware activespm --storage spm --weights none \
  --mode serial --check perf --warmup 1 --repeats 5 \
  --soc-mhz 50 --timeout-sec 1800 \
  --report reports/B2_weights_none_perf.json
```

```bash
python host_resnet18_activespm_weights.py \
  --hardware activespm --storage spm --weights resident \
  --mode serial --check perf --warmup 1 --repeats 5 \
  --soc-mhz 50 --timeout-sec 1800 \
  --report reports/B3_weights_resident_perf.json
```

```bash
python compare_weights.py \
  --baseline reports/B2_weights_none_perf.json \
  --resident reports/B3_weights_resident_perf.json \
  --out reports/B3_vs_B2_weights.json
```

输出整网加速比、平均批次/单图时间、初始化增量及逐层变化。终端单列常驻权重对应层，JSON 保留全部 30 个节点。加速比小于 1 也是有效结果，程序不保证加速。

PERF 核对最后 measured 轮的四份完整 logits；每层每轮检查使用 DEBUG。两种模式都在计算计时外检查常驻权重。逐层 operator 计时之和应与目标 operator_cycles 一致；批次计时还包含调度、指针选择、计数存储、栅栏和同步。

## 5. 初始化与首次批次成本

报告分别记录：

| 字段 | 范围 |
|---|---|
| `copy_cycles` | 每个参与 hart 的权重复制、保护区安装及结束栅栏 |
| `verify_cycles` | 模型执行前，权重和保护区逐字节校验 |
| `setup_cycles` | 上述权重准备流程总时间 |
| `final_verify_cycles` | 所有轮次完成后再次校验 |
| `setup_makespan_cycles` | hart0 配置与控制检查后、发布启动握手前开始准备，到首轮 GO 前的墙钟周期，包含工作区保护区、复制、初始校验和等待另一 hart |
| `iteration_cycles` | 每个 measured 四图批次的计算时间，排除准备、最终校验和 Host 传输 |

`setup_makespan_cycles` 不包含 ELF 加载、BSS 清零和前面的启动过程。程序没有强制清空 L2，因此不声称冷缓存测量。

为了直接测量“准备＋首次四图批次”，分别复位后运行：

```bash
python host_resnet18_activespm_weights.py \
  --hardware activespm --storage spm --weights none \
  --mode serial --check perf --warmup 0 --repeats 1 \
  --soc-mhz 50 --report reports/B2_weights_none_first.json
```

```bash
python host_resnet18_activespm_weights.py \
  --hardware activespm --storage spm --weights resident \
  --mode serial --check perf --warmup 0 --repeats 1 \
  --soc-mhz 50 --report reports/B3_weights_resident_first.json
```

```bash
python compare_weights.py \
  --baseline reports/B2_weights_none_first.json \
  --resident reports/B3_weights_resident_first.json \
  --out reports/B3_vs_B2_first.json
```

这时 `setup_plus_first_batch_cycles` 是准备加首次批次时间，不是完整 START 到返回预测的端到端延迟，也不保证冷缓存。持续运行比较的摊销批次数由“额外准备成本/平均每批节省”估计，不是实测回本点；没有每批节省时不报告有限回本批次数。

## 6. 后续改选层

先保留本轮完整目录、生成数据、ELF、Host 包和报告。新计划须重新生成、编译、打包、解压，不能混用旧 Host 包。

第二个候选 `layer2-large`：只放 `layer2.0.main_preadd`、`layer2.1.conv1_relu`、`layer2.1.main_preadd` 三组权重。载荷 442,368 B，加保护区后总占用 1,045,248 B，约 99.68%。容量利用率更高不代表更快。

```bash
/home/cuixh/.venv/bin/python prepare_weights.py \
  --full-generated /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/full/generated \
  --weight-profile layer2-large --out generated
```

也可指定完整权重对应节点；超容量或节点不存在会拒绝生成：

```bash
/home/cuixh/.venv/bin/python prepare_weights.py \
  --full-generated /home/cuixh/chipyard/tests/vcu128_resnet18_ptq/full/generated \
  --weight-profile manual \
  --weight-nodes layer3.0.conv1_relu,layer4.0.skip_projection \
  --out generated
```

每组候选分别运行匹配的 none/resident 对照。严格脚本不直接混比不同 ELF 的候选。先根据逐层报告调整，不只追求填满。

## 7. 最终与 2 MiB cache-only 比较

B3 验证后，切换到原 cache-only bitstream，使用本次相同 ELF：

```bash
python host_resnet18_activespm_weights.py \
  --hardware cacheonly --storage hbm --weights none \
  --mode serial --check perf --warmup 1 --repeats 5 \
  --soc-mhz 50 --timeout-sec 1800 \
  --report reports/B0_cacheonly_weights_runtime_perf.json
```

```bash
python compare_performance.py \
  --b0 reports/B0_cacheonly_weights_runtime_perf.json \
  --candidate reports/B3_weights_resident_perf.json \
  --out reports/B3_vs_B0_equal_capacity.json
```

脚本会把常驻权重版本标记为 B3，结果保留实际 `weight_mode=resident`。此处按单组参与容量比较 `2 MiB L2` 与 `1 MiB L2＋所使用的1 MiB SPM`，不声称物理等面积。ActiveSPM 硬件仍有另一实例，其物理数量和资源在报告中记录。双 hart 时参与容量是 3 MiB，不与原 2 MiB B0 作等容量比较。

## 8. 验证范围

```bash
make check PYTHON=/home/cuixh/.venv/bin/python
```

本地测试覆盖布局与生命周期、跨图权重共享、单/双 hart、swap、重复执行、闲置 hart 不访问 SPM、权重与保护区故障注入、计时/报告一致性、ELF/manifest 绑定及 Host 打包。合成数据明确标记，Host 拒绝其上板；native 后端用于控制和地址流测试，不等于真实 Gemmini 数值验证。

交付前用真实 RISC-V 编译器和已验证版本 Gemmini 头文件编译了实际算子后端，并用合成模型数据完成 ELF 绑定检查。真实权重的板上 DEBUG 与性能仍需按本文执行。本包不分发合成 ELF，也没有 HBM 流量计数器，不能将时延改善直接表述为实测 HBM 字节数减少。
