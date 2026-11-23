# 交付验证记录

- 39 项本地自动测试通过，包括原 HBM/SPM 路径、权重常驻、single/dual/hart1/swap、重复执行、闲置实例不访问、权重数据与保护区故障注入。
- early、layer2-large、manual 三种选层方式均通过 native 运行时测试。
- 每个阶段的权重指针与原始 blob/常驻副本绑定检查通过；逐层计时之和、状态协议与报告契约通过。
- RISC-V GCC 13.2.0 使用已验证的 Gemmini 头文件，实际编译 start.S/runtime/resident_weights/platform/model_backend；链接和 ELF/manifest/blob/权重表绑定 dry-run 通过。
- Host 包生成通过；合成模型被明确标记，Host 拒绝其上板。
- model_backend.c 与 resadd_direct.h 的 SHA256 与此前用户板上 B2 PASS 报告一致。

本地 runtime 测试使用合成数据和控制流后端，不代表本次真实模型的 Gemmini 数值测试已经通过。真实 full_blob 不在此源码包内；上板须从用户已有 full export 重新生成、编译并执行 README 中 DEBUG。

性能收益尚未测量，不预设常驻权重一定加速。初始化复制与初始/最终逐字节校验均单独计时；首次批次不宣称冷缓存。
