# 交付前验证

以下结果均为本地离线检查，不是用户 VCU128 上的并行结果。

- 直接复用已通过完整单核阶段的四图/120 步导出物；INT8 权重、输入和 golden 字节保持相同。
- 每任务独立 2,642,624 B arena，共 10,570,496 B；所有图边指向本任务真实前层输出。
- RISC-V GCC 13.2、RV64 IMAFDC+Zicsr/Zifencei、lp64d 编译双核 ELF；本地 image_end 为 `0x81fe8540`，其他工具链构建可变化。
- ELF 检查通过双栈、APP/BSS、独立 arena/结果表、完整 blob、图表中所有真实地址和 manifest 摘要的绑定。
- 同一份 C runtime 配合两个 POSIX 线程、独立 INT64 标量模型，串行、正常并行、交换任务三种模式全部通过：每模式四图、120 个真实连接步骤，严格匹配全部中间结果、最终 logits 和保护区。
- 原生并行两种分配均观察到计算区间重叠；串行模式确认 hart1 没有调用模型。原生时钟不作为 FPGA 性能结果。
- 原生错误注入检查：修改一份最终输出，runtime 检测并发布 ERROR。
- Host 离线拒绝测试覆盖错误 epoch、错误核/任务归属、未完成状态、错误 task metadata/checksum/输出地址，以及全部 120 个张量的字节差异。
- 报告比较逻辑检查通过正常速度比计算，并拒绝不同 ELF 或不同完整 logits。
- Python 语法检查通过；Host 独立打包/解压后 ELF/数据绑定检查通过。

未执行 Gemmini RTL 仿真、Gemmini ISA 仿真或真实双 Gemmini 并行计算。
板上正确性和速度比必须按 README 的正常并行、交换分配、串行对照执行后确认。
