# 交付前验证

以下是本地离线验证，不是 VCU128 板上通过记录。

- 使用前阶段真实 ResNet18 PTQ 参数，以及原来的四张图片整数参考。
- 导出 20 Conv（含 3 个投影）、8 Add、1 GAP、1 FC：每图 30 个检查点，共 120 个。
- blob 为 22,859,360 B；带保护区的激活 arena 为 2,642,624 B。不同数据集大小会变化。
- 原生 GCC 独立 C 标量整图执行通过全部 120 个检查点：使用实际前层输出，INT64 累加，当前 C 缩放宏，严格匹配所有中间结果和最终 INT32 logits。
- 每层执行后检查全部 tensor 保护区；此检查不模拟 Gemmini 硬件。
- 使用 RISC-V GCC 13.2、RV64 IMAFDC+Zicsr/Zifencei、lp64d 编译两份 ELF。
- 两份 ELF 均通过 PT_LOAD、入口/栈/BSS、构建身份、完整 blob 字节、manifest 摘要和全部 C 图描述符/arena 指针绑定检查。
- 两核各 120 项 Host 验证逻辑正确接受 golden，并拒绝错误输出、保护区、hart、输出地址；图校验拒绝 golden 注入为前层输入、错误拓扑连接和缓冲区重叠。
- Python 语法检查通过；独立 Host bundle 打包/解压后检查两份 ELF 与当前 manifest/blob 绑定。
- 未执行 RTL 仿真、Gemmini ISA 仿真或 VCU128 板上整网推理。

本地离线 GCC 额外指定了配套 newlib 头路径；用户应沿用刚才已经成功编译算子微测试的 Chipyard 环境。
不会自动修改用户的环境、Gemmini 驱动、RTL 或比特流。

这是调试验证版本：包含逐层比较、完整输出读回、Host ACK 和保留全部张量。计时不可直接当作最终连续推理性能。
