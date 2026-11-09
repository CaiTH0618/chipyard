# VCU128 双 Rocket + 双 Gemmini：单核 ResNet50 上板软件

本目录放在 `/home/cuixh/chipyard/tests/vcu128_resnet50`，与旧的
`tests/vcu128_dual_gemmini` 并列。编译在 Chipyard 的 `.conda-env` 中完成。
目标是 hart0 使用自己的 Gemmini 执行仓库现有 INT8 ResNet50；hart1 在入口等待。
这是软件适配包，尚未在你的板上验证。综合、实现、生成比特流仍使用已经切换的新 RTL。

## 1. 已依据你上传的文件核定的接口

- Rocket 可访问的主存窗口是 `[0x80000000, 0x90000000)`，共 256 MiB。
- Host 使用原来成功的 XDMA → FBus → SoC 内存系统 → HBM 通路。
  `/dev/xdma0_h2c_0` 和 `/dev/xdma0_c2h_0` 的偏移仍使用 **Rocket 地址**。
  仅在 `axi64_to_hbm256.sv` 内减去 `0x80000000`，Host 不再减一次。
- 控制器 ID 为 `0x534f4301`；STATUS `0x08` 的 bit1 表示基础设施就绪，
  bit0 表示本轮 START 已使用；向 `0x0c` 写 1 启动。
- 你上传的控制器 **没有软件 RESET 寄存器**。不能向 `0x10` 写复位。
  每次硬件全路径复位后只接受一次 START。更换 ELF / 重跑必须使用旧项目验证过的
  完整复位流程，使 Rocket、缓存、控制器和相关路径回到初始状态；不能只清结果区。
- 代码使用现有 FBus 访问路径，沿用提供的 Gemmini 驱动完成/内存顺序约定。
  不通过另一个绕过 SoC 缓存的 HBM 直连端口读取结果。

地址窗口来自适配 RTL 和 DTS，但高地址在实板上的可达性尚待测试。
Host 启动前会先写入多个相距 64 MiB 的不同图样，再全部读回，检查高地址及地址别名。
这个探测会覆盖几个测试位置，只用于尚未 START 的当前实验。

## 2. HBM 地址布局

下表为半开区间，即终点地址不包含在区域内。

| 用途 | Rocket / Host 起点 | 终点 | 说明 |
|---|---|---|---|
| ELF 代码、权重、图片、数据、BSS | `0x80000000` | `0x8f000000` | 240 MiB，链接器强制检查 |
| 预留 | `0x8f000000` | `0x8fe00000` | 保留间隔及 guard |
| hart0 栈 | `0x8fe00000` | `0x8ff00000` | 1 MiB，向低地址增长 |
| 结果区 | `0x8ff00000` | `0x8ff04000` | 16 KiB，含状态和 4000 个 int8 输出 |
| 日志区 | `0x8ff10000` | `0x8ff20000` | 64 KiB 环形缓冲区 |

未列出的其余空间预留。Host 在 APP 边界、栈底、结果区外、日志区前后放置 guard。
旧测试的 `0x80010000` 输入区、`0x80030000` mailbox 不再保留；它们会落在大模型镜像中。
不要使用旧 Host 向这些位置写描述符或等待结果。

## 3. ELF 从哪里来，是否需要单独搬权重

`prepare_model.py` 从你本机 Gemmini 目录读取 `imagenet/resnet50.c`、
`resnet50_params.h`、`images.h` 和驱动头文件，复制到本模式的 `build/.../inputs`，
记录 SHA256，再生成带状态记录的模型 C 文件。它不下载文件，不修改 Gemmini 原始源码。
编译把代码、常量权重和预置图片一起链接进 ELF；中间激活等未初始化数组放在 BSS。

因此第一阶段只需要 Host 装载 ELF 的文件数据，再启动 Rocket 清 BSS 并推理；
**不需要另外上传 `.pth`，也不需要另写一份权重搬运程序**。
后续换输入图片，可再设计独立输入缓冲区和命令协议。

INT8 权重和激活、INT32 累加不代表完全不使用浮点。
当前软件有 float 缩放系数，Rocket DTS 支持 F/D，本包使用 `lp64d` 并在启动时开启 FS。
Gemmini 的实际量化缩放硬件来自你保留的 defaultConfig 缩放配置。

为防止对不同版本源文件错误插桩，脚本核验你上传的 `resnet50.c`、`gemmini.h`、
`gemmini_nn.h` 的 SHA256。若版本不符则停止，不要删除这个检查强行继续。
`gemmini_params.h` 必须匹配 DIM=8、custom3、4 个 scratchpad bank、
BANK_ROWS=2048、ACC_ROWS=1024、int8/int32。
不要复制 `libgemmini` 中 DIM=16 的另一份头文件。

归档中没有再次包含大权重和图片；编译直接用你 Chipyard 目录中的现有文件。
`compat/.../xcustom.h` 仅提供已提供驱动需要的两种 RoCC 指令宏，输入指令编码沿用
旧项目已验证的 `.insn` 形式，不依赖上传包未包含的 rocc-software 子模块。

## 4. 安装与第一次编译：先只做 init

将软件包保存到服务器，例如 `/home/cuixh/vcu128_resnet50_software.tar.gz`。
在已有 Chipyard Conda 环境终端执行：

```bash
cd /home/cuixh/chipyard
tar -xzf /home/cuixh/vcu128_resnet50_software.tar.gz

export CHIPYARD_ROOT=/home/cuixh/chipyard
export GEMMINI_SW="$CHIPYARD_ROOT/generators/gemmini/software/gemmini-rocc-tests"

command -v riscv64-unknown-elf-gcc
python3 -m pip install -r tests/vcu128_resnet50/requirements.txt

cd /home/cuixh/chipyard/tests/vcu128_resnet50
python3 test_offline.py
make MODE=init
```

输出为 `build/init/resnet50_init.elf`、`.map` 和 `inputs/input_hashes.json`。
Makefile 最后自动执行 ELF 离线检查：入口、RV64 ABI、可装载段、BSS、栈和协议元数据；
看到 `DRY RUN PASS` 说明镜像布局检查通过，尚未打开板卡设备。

init 模式保留完整模型函数及其引用的权重/图片/激活数组，只跳过实际调用。
它可以先验证“大程序装载 + 大 BSS 初始化 + hart0 启动 + Host 读结果”。
没有用小程序冒充大模型初始化检查。

如果工具链前缀不同，可指定，例如：

```bash
make MODE=init CROSS=/实际工具链目录/bin/riscv64-unknown-elf-
```

如旧工具链不识别单列的 Zicsr/Zifencei，但其 `rv64gc` 支持相应指令，可按旧项目版本使用
`make MODE=init ARCH=rv64gc`。不能为绕过错误随意改 ABI。

可选反汇编：`make MODE=init inspect`，只在主动执行时生成 `.dis`。

## 5. Vivado 实现结束后的上板顺序

先确认新配置的实现和时序检查通过，再生成并下载新比特流。
沿用旧工程已经验证的下载、PCIe 恢复/驱动绑定和完整复位流程；本包不猜测你的复位命令。
换 RTL 后先用旧双 Gemmini 小测试做一次回归也可以，但它会消耗一次 START，
之后跑新 ELF 前仍需完整复位。

在能访问 `/dev/xdma0_*` 的 **板卡 Host 机器** 上运行 Host 脚本。
它不一定与编译所在的 PVE 是同一台机器；如果不同，把本测试目录和编译好的 ELF
复制到 Host，并在 Host 的 Python 环境安装 pyelftools。

第一轮，复位后只测试高地址（不发 START；可在同一轮继续 init）：

```bash
cd /home/cuixh/chipyard/tests/vcu128_resnet50
sudo "$(command -v python3)" host_resnet50_test.py --probe-only
```

预期 `PROBE ONLY PASS`。若失败，先排查 XDMA→FBus 路由/地址位宽/高地址访问，
不继续推理。探测只证明采样地址可读写及所测别名不存在，不等同于全容量内存测试。

然后运行完整镜像初始化：

```bash
sudo "$(command -v python3)" host_resnet50_test.py \
  --elf build/init/resnet50_init.elf --timeout-sec 1800
```

Host 默认逐块装载并读回校验所有文件数据。BSS 默认由 Rocket 清零，避免传输大量零；
需要额外排查 BSS 时可加 `--zero-bss`，让 Host 也先清一次。
最后应为 `PASS INIT ONLY`，这不表示模型预测正确。

## 6. 首层执行与完整推理

init 通过后编译首层版本：

```bash
make MODE=first
```

使用旧工程流程完成一次全路径复位，再执行：

```bash
sudo "$(command -v python3)" host_resnet50_test.py \
  --elf build/first/resnet50_first.elf --timeout-sec 1800
```

这会执行首层卷积和融合池化，记录 `4×56×56×64=802816` 字节输出的校验和，
随后停止。`PASS FIRST-LAYER EXECUTION` 表示首层执行并正常返回，
**这一模式没有 CPU 数值参考，不宣称首层数值正确**。

再编译完整版本：

```bash
make MODE=full
```

再次完成全路径复位，然后：

```bash
sudo "$(command -v python3)" host_resnet50_test.py \
  --elf build/full/resnet50_full.elf --timeout-sec 3600
```

完整模式保留原模型的卷积、残差、全局平均和最终全连接调用。
最后将 `[4][1000]` 个 int8 输出保存到结果区，Host 重新计算 argmax、校验和并检查 guard。
预置四张图片的原程序参考类别依次为 `75, 900, 641, 897`。
遵循原程序规则：若量化造成最高分并列，参考类别得分等于最高分也接受。
Host 额外拒绝整条输出向量恒定的退化结果。

`PASS FULL RESNET50` 才表示本组输入完成推理并通过上述检查。
这些 int8 输出是量化分类分数，不是概率，也不构成 ImageNet 全数据集准确率评估。
默认 1800 秒/示例 3600 秒为工程超时设置，不是性能承诺。

## 7. 状态、错误和复现记录

Host 每次生成不同的 run_id，拒绝把上轮结果当成本轮成功。
状态包括 PREPARED=1、RUNNING=2、DONE=3、ERROR=4；结果 state 在数据提交后最后写入。
阶段 1 为入口，2 为 BSS 清零，3 为 C 运行时就绪，101–154 对应模型层，
201 为首层采集，300 为分类，400 为完成，65535 为异常。

异常处理写回 `mcause/mepc/mtval` 后等待；若程序在异常处理器可访问结果区之前失败，
仍可能表现为 Host 超时。超时输出最后读到的阶段和完整头信息。
Host 不写任何不存在的软件复位寄存器。

计时字段 `cycles_total` 从进入模型到输出/分类提交，包含软件调度和部分结果处理；
`cycles_ops` 是原程序各类算子计时之和。init 无模型周期，first 暂不汇总算子周期。
不能直接把这两个字段都解释为 Gemmini 纯计算周期；换算时间要用实际 SoC 时钟。

每个模式的 `build/.../inputs/input_hashes.json` 保存本次权重、图片和头文件指纹。
它与 `.elf`、`.map`、对应的 Scala 配置和比特流一起留存，便于复现。
不要在同一模式目录中并行运行两次 make；不同模式可分别编译。

如果改 Gemmini 数组大小、容量或量化数据类型，需要更新对应软件布局核验并重新编译，
不能直接复用旧 ELF。当前包专门匹配本项目 8×8 配置。

## 8. 文件作用

| 文件 | 作用 |
|---|---|
| `start.S` | 停放 hart1、设置栈/FP/异常入口、清 BSS、进入 C |
| `linker.ld` | 240 MiB APP 布局及独立栈、链接越界检查 |
| `prepare_model.py` | 冻结本地模型与头文件，插入阶段和输出采集 |
| `platform_runtime.c` | 最小裸机运行时、HBM 日志、状态/异常/结果提交 |
| `result_layout.h` / `protocol.py` | 目标端和 Host 共用的结果布局约定 |
| `host_resnet50_test.py` | 地址探测、ELF 装载读回、START、日志与结果验证 |
| `test_offline.py` | 无板卡协议、错误结果、地址别名及分块装载测试 |

离线复查：`python3 test_offline.py build/full/resnet50_full.elf`。
本包的本地编译验证使用你提供的驱动/模型源码和公开同名参数头作编译样本，
不代替你本机权重版本的编译结果，也没有执行 FPGA 上的模型推理。

已完成的本地验证：GCC 13.2.0 / binutils 2.42 下 init、first、full 三个版本
均交叉编译成功并通过 ELF dry-run；init 和 full 保留约 26.25 MB 文件数据及
42.06 MB BSS，链接终点为 `0x841246c0`（你的工具链和权重版本可能不同）。
8 项无设备测试、真实 ELF 元数据/错误入口拒绝测试，以及 C 结构体布局静态断言通过。
