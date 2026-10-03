# Changelog

## 0.1.0

- 初始化独立项目和 Python/SQLite 基础设施。
- 增加 schema version 1、任务范围快照、状态枚举、脱敏日志和离线 CLI。
- 增加 P1-P3 范围标准化、动作计划、Mock/FOFA 适配器、观察/证据/资产/缺口及报告重建。
- 增加 P4 能力矩阵、安全主动适配器、架构门控，以及 P5 checkpoint 和未知终态恢复。
- 增加 P6 子任务委派恢复、lease/heartbeat、幂等结果提交和 Playwright 外部适配器。
- schema version 更新至 3，记录 P6 checkpoint。
- 增加 P6 后恢复硬化：FOFA checkpoint 续跑、动作终态、暂停/取消停止、报告恢复摘要和运行模式参数。
- 增加最终验收质量门和重复运行幂等测试，防止不完整任务误报 `complete`。
