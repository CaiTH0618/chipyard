# VCU128 双 Rocket：共享内存通信与同步实验

将本工程放到 `/home/cuixh/chipyard/tests/vcu128_dual_sync`。
复用已跑通 ResNet50 的双 Rocket＋双 Gemmini 比特流；本软件不发送 Gemmini/RoCC 指令。
四项实验由两个 Rocket 自主完成。Host 负责装载、一次 START、进度读取和最终验证，
不参与逐轮消息传递。

本包包含源码与操作说明，请在你的 Chipyard 环境自行编译 ELF。
已经完成本地交叉编译和本机双线程软件验证，尚未在你的 FPGA 上执行。

## 1. 四项实验

一个 `dual_sync.elf` 支持以下运行模式；通过 Host 的 `--test` 选择，无需反复编译。

| `--test` | 默认规模 | 验证内容 |
|---|---|---|
| `ping` | 10,000 轮 | hart0 写 64 位请求，hart1 验证并回复，hart0 验证回复 |
| `block` | 96 轮 | 每轮依次循环 64 B、4 KiB、64 KiB 数组，逐元素检查请求和变换后的应答 |
| `barrier` | 1,000 轮 | 每核写 8 个 64 位值，屏障后检查另一核，第二道屏障后才可覆盖下一轮 |
| `atomic` | 每核 100,000 次 | 两核原子递增同一 64 位计数器，最终必须为 200,000 |
| `all` | 以上全部 | 一次 START 按 ping→block→barrier→atomic 顺序完成 |

输入随 seed、实验编号、轮次和元素下标变化，避免旧值碰巧匹配。
目标端对每一个消费到的数据元素进行比较，并累计轮次和 64 位摘要。
Host 独立重算全部轮次的预期摘要，另外读取最终消息/缓冲区/屏障贡献并检查。
摘要是非加密的一致性检查，并不构成所有内存交错情况的形式化证明。

## 2. 现有接口与复位约定

- 程序入口仍为 `0x80000000`，沿用此前 BootROM/custom_boot 启动路径。
- 两核都使用原先主存地址窗口 `[0x80000000, 0x90000000)`。
- Host DMA 偏移使用 **CPU/FBus 地址**，沿用成功的 XDMA→FBus→SoC 内存系统→HBM 通路。
  不在 Python 中减掉 `0x80000000`；后端 RTL 自行转换。
- BAR 控制器 ID `0x534f4301`；STATUS `0x08` bit1 为 ready，bit0 为 START 已使用。
  向 `0x0c` 写 1 启动。
- 当前控制器没有软件 RESET 寄存器。本程序不向 `0x10` 写入。
  **每轮测试前使用之前已验证的全路径复位流程。** 仅重启 Python、清结果区或
  更换 ELF 不能代替 Rocket/缓存/控制路径的完整复位。
- 当前软件是独立实验镜像，不与旧 ResNet50 同时运行。新的共享区可能覆盖旧模型地址。

## 3. 地址布局

所有区间均为半开区间，表中终点不包含在内。

| 区域 | 起点 | 终点 | 说明 |
|---|---|---|---|
| APP | `0x80000000` | `0x80100000` | 代码、常量和小 BSS；链接器检查越界 |
| hart0 栈 | `0x80110000` | `0x80120000` | 64 KiB，独立栈 |
| hart1 栈 | `0x80130000` | `0x80140000` | 64 KiB，独立栈 |
| 共享控制/结果区 | `0x80200000` | `0x80201000` | 4 KiB，不在 BSS 中 |
| 请求数组 | `0x80300000` | `0x80310000` | 最多 64 KiB |
| 应答数组 | `0x80400000` | `0x80410000` | 最多 64 KiB |

Host 在 APP 上限、每核栈底、控制区和缓冲区周围写入 64 字节 guard，完成后检查。
这些 guard 检查的是布置的边界位置，不是任意越界写的完整检测。

共享区的主要偏移：

| 相对 `0x80200000` 的偏移 | 内容 | 写入方 |
|---|---|---|
| `0x000` | 128 字节配置，含 run_id/seed/轮数 | Host，仅 START 前写 |
| `0x080` | bootstrap epoch | hart0 |
| `0x0c0` | request_seq | hart0 |
| `0x100` | response_seq | hart1 |
| `0x140` / `0x180` | 每核屏障到达序号 | 各核只写自己一项 |
| `0x1c0` | 原子计数器 | 两核原子读改写 |
| `0x200` / `0x240` | ping 请求/应答数据 | 协议约定的单写方 |
| `0x280` / `0x2c0` | 每核屏障贡献数据 | 各核只写自己一项 |
| `0x400` / `0x500` | 各 256 字节的每核结果 | 各核只写自己结果 |

热点序号分别占一条 64 字节缓存行。数据区、控制区和结果区由 `layout.h` 与
`protocol.py` 共同约定；修改时必须保持一致。

## 4. 编译：在 Chipyard Conda 环境中执行

将压缩包保存到服务器，例如 `/home/cuixh/vcu128_dual_sync_software.tar.gz`。

```bash
cd /home/cuixh/chipyard
tar -xzf /home/cuixh/vcu128_dual_sync_software.tar.gz

cd /home/cuixh/chipyard/tests/vcu128_dual_sync
python3 -m pip install -r requirements.txt

command -v riscv64-unknown-elf-gcc
make
```

默认 ISA 为 `rv64imac_zicsr_zifencei`，ABI 为 `lp64`，没有浮点调用或 Linux 运行时依赖。
如工具链前缀不在 PATH：

```bash
make CROSS=/实际工具链路径/bin/riscv64-unknown-elf-
```

生成：

| 文件 | 用途 |
|---|---|
| `build/dual_sync.elf` | 两核共用的裸机程序 |
| `build/dual_sync.map` | 链接布局 |
| `build/dual_sync.dis` | 反汇编，可查看 fence、AMO 和启动代码 |

编译后自动检查：

```text
INSTRUCTION CHECK PASS: inline amoadd.d/fence, per-hart entry, no RoCC opcodes
DRY RUN PASS: ELF layout, ABI, symbols and metadata; no device access
```

ELF 检查包括入口、软浮点 ABI、两个独立栈、BSS、元数据、装载段范围，以及不存在
未解析的原子运行库函数。程序不能被 strip 后再交给当前 Host 脚本，因为它还需要符号表核验。
如果只修改 Host 脚本，可手动执行 `make dry-run` 再核验现有 ELF。

## 5. 无板卡的软件检查

```bash
make check
```

该命令用本机 GCC/pthreads 编译 `dual_sync.c` 和 `native_runner.c`，实际运行同一份
四项实验算法，再用 Python 独立验证输出。它不会打开 `/dev/xdma*`，不会启动板卡。
需要本机 C 编译器；可用 `make check HOSTCC=/某路径/gcc` 指定。

覆盖默认全负载、单项/组合模式、不同 seed、延迟启动、单轮边界、10,000 轮屏障复用、
缺失另一核超时、错误配置、错误计数器初值，以及 Host 拒绝损坏/过期/未完成结果。
构建过 RISC-V ELF 时，还检查真实 ELF 和错误入口/截断文件的拒绝行为。

**本机双线程测试不能替代 RISC-V 内存顺序、FPGA 缓存一致性、BootROM 或 XDMA 上板验证。**
本机平台时间来自纳秒时钟，native 测试不用于测量 Rocket 性能。

## 6. 复制到 FPGA 工程的运行目录

在编译终端执行：

```bash
TEST_DIR=/home/cuixh/chipyard/tests/vcu128_dual_sync
RUN_DIR=/home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet50/software/dual_sync

mkdir -p "$RUN_DIR"

cp "$TEST_DIR/build/dual_sync.elf" \
   "$TEST_DIR/host_dual_sync_test.py" \
   "$TEST_DIR/protocol.py" \
   "$TEST_DIR/xdma_transport.py" \
   "$TEST_DIR/requirements.txt" \
   "$TEST_DIR/README.md" \
   "$RUN_DIR/"
```

Host 必须同时有 `protocol.py` 和 `xdma_transport.py`。
如果编译机器和板卡 Host 不同，将这些文件复制到连接 VCU128 的 Host。

## 7. 上板：先小规模 ping，再运行全部

继续使用已经成功部署的双核比特流，不需要重新生成 RTL。
在 Host 终端激活之前成功运行 ResNet50 Host 的 `.venv`，完成一次全路径复位。

```bash
cd /home/cuixh/xilinx_proj/vcu128_xdma_chipyard_dualrocket_dualgemmini_resnet50/software/dual_sync
python3 -m pip install -r requirements.txt

python3 host_dual_sync_test.py --elf dual_sync.elf --test ping --ping-rounds 100
```

如设备权限需要 sudo，则使用 `sudo "$(command -v python3)" ...` 保持当前环境的 Python。
Host 会自动检查 ready/START、探测本实验地址、装载 ELF 并读回校验，再初始化共享区、
用不同图样填充请求/应答缓冲区，布置 guard，最后发 START。

预期最后出现：

```text
PASS PING
PASS DUAL ROCKET SYNC: both harts, round counts, data digests, final payloads and guards
```

完成一次全路径复位，再执行全部默认负载：

```bash
python3 host_dual_sync_test.py --elf dual_sync.elf --test all --timeout-sec 120
```

预期最后包含：

```text
PASS PING
PASS BLOCK
PASS BARRIER
PASS ATOMIC
Atomic counter verified: 200000
PASS DUAL ROCKET SYNC: both harts, round counts, data digests, final payloads and guards
```

阶段和进度为定期采样，短阶段可能不会单独显示；最终逐项 PASS 由计数和数据核验决定。
每核 cycles 统计包括软件计算图样、检查、等待、主动延迟和屏障，不能直接解释为
裸硬件链路延迟。初版以功能验证为目标。

每次发出 START 后，下一次测试都要先完整复位。
修改 `--seed` 或测试轮数后不需要重新编译，只需复位再运行。

单项命令如下，**每条是独立的一轮实验，不能未经复位连续执行**：

```bash
python3 host_dual_sync_test.py --elf dual_sync.elf --test block --block-rounds 96
python3 host_dual_sync_test.py --elf dual_sync.elf --test barrier --barrier-rounds 10000
python3 host_dual_sync_test.py --elf dual_sync.elf --test atomic --atomic-iters 1000000
```

可选复现参数及结果保存：

```bash
python3 host_dual_sync_test.py --elf dual_sync.elf --test all \
  --seed 0x87654321 --delay-cycles 2000 --result-json dual_sync_result.json
```

不会默认产生日志文件。`--result-json` 仅在验证通过后写入配置及每核结果。

## 8. 协议为什么这样实现

### 启动

两核从同一个 `_start` 入口启动，设置各自的栈。只有 hart0 清 APP BSS。
hart1 可进入只依赖私有栈和共享配置的 C 引导代码，等待 hart0 发布 bootstrap epoch。
共享配置/epoch 位于固定主存区域，独立于 APP BSS，不会被清 BSS 覆盖。
hart1 acquire 观察到本轮 epoch 后才继续；目标端没有使用共享可写的 C 运行时全局状态。
后续扩展时，不要在二级核等待 bootstrap 之前加入对其他 BSS 全局变量的访问。

### 请求/应答

序号编码为 `(test_id << 32) | round`。
hart0 写完请求后 release 发布 request_seq；hart1 acquire 观察后读取，完成应答再 release
发布 response_seq。hart0 acquire 观察并读完应答后，才发布下一轮请求。
因此双方都不会在对方仍在读取时覆盖旧缓冲区。
单纯 volatile 不代替这个同步协议。

### 可重复屏障

每核只写自己的单调递增 arrival ticket，release 发布后 acquire 等待对方 ticket **大于等于**
本地 generation。对方可能已经到达下一道屏障，因此不能只等待相等。
数据校验后再使用第二道屏障，确保快核不能提前覆盖下一轮贡献。
该协议仅为两核设计，不是通用 N 核 barrier。

### 原子计数

counter 在 START 前初始化为零。两核使用 `__atomic_fetch_add(..., __ATOMIC_RELAXED)`
递增；循环后的 acquire/release 屏障保证双方计数操作完成后再核验总数。
RELAXED 适用于这里的计数递增；消息发布仍使用 RELEASE/ACQUIRE。
GCC 版本不同，release store 可能生成普通写加 fence，也可能生成 amoswap 加 fence；
本包不假定它只使用普通 store。专门的计数实验检查 amoadd 的累计结果。

参考：[GCC __atomic 内建操作文档](https://gcc.gnu.org/onlinedocs/gcc/_005f_005fatomic-Builtins.html)。

## 9. 错误定位

每核状态：PREPARED=1、RUNNING=2、DONE=3、ERROR=4。
每核结果区记录实验、轮次、错误下标、期望/实际值，以及 mcause/mepc/mtval。
出错时先提交细节，再提交 ERROR；另一核在等待中检查对方错误并退出。
目标端没有使用 WFI 等待消息，避免在未启用中断时睡眠后无法被共享内存写唤醒。

| 错误码 | 意义 |
|---|---|
| 1 | 配置非法 |
| 2 | 等待序号或屏障超时 |
| 3 | 对方已报错，本核退出 |
| 4 | 实际数据不匹配 |
| 5 | 序号异常跳到预期轮次之后 |
| 6 | 原子计数结果不匹配 |
| 7 | RISC-V 异常，查看 CSR 信息 |
| 8 | 启动 BSS 清零检查失败 |

`--wait-cycles` 默认 500,000,000，50 MHz 时每次等待约允许 10 秒，25 MHz 时约 20 秒。
这是每次等待的超时，不是整个测试的总超时。`--timeout-sec` 是 Host 的总等待时间。
若测试期间时钟暂停或硬件访问本身卡住，目标端周期超时不一定能执行，Host 仍会总超时。
若怀疑是轮数太大，先减小规模，保留最后每核进度及错误信息。

## 10. 代码阅读顺序与当前验证范围

建议依次阅读 `layout.h` → `start.S` → `dual_sync.c` → `protocol.py` →
`host_dual_sync_test.py`。`platform.h` 提供目标板和本机测试的轻量平台接口，
`native_runner.c` 只参与无设备验证，不链接进 RISC-V ELF。

已在本地 GCC 13.2.0 / binutils 2.42 下完成交叉编译，验证内联 AMO/fence 和无 RoCC 指令；
本机双线程测试运行同一份 C 算法。FPGA 实测通过后，才能确认这块板子的双核通信、
同步和原子操作功能通过本组用例。
尚未测试中断通知、Gemmini 输出交接、完整模型并行或任意线程数的同步。
