# Architecture

依赖方向固定为 `CLI -> Coordinator/Policy/State -> Adapter -> external tool`。
适配器只返回结构化结果，不修改全局资产、预算或任务完成状态；报告从持久化状态重建。

P0 建立任务/动作/范围/事件/checkpoint 基础表及安全状态枚举（schema v1 起向后兼容，
已演进至 v12——演进记录见 SCHEMA.md 与 `state/database.py`）。
