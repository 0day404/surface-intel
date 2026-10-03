# Architecture

依赖方向固定为 `CLI -> Coordinator/Policy/State -> Adapter -> external tool`。
适配器只返回结构化结果，不修改全局资产、预算或任务完成状态；报告从持久化状态重建。

P0 已冻结 SQLite schema version 1、任务/动作/范围/事件/checkpoint 基础表及安全状态枚举。
