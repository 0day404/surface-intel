# Schema

当前 schema version 为 `1.0`，SQLite migration version 为 `3`。

适配器能力状态使用 `available`、`degraded`、`not_configured`、`unsupported`
和 `failed`。能力状态是预检和报告字段；`not_configured`、`unsupported` 或
`failed` 不能被替换成 Mock 成功结果后继续声明真实能力。

实体：`task`、`scope_snapshot`、`action`、`event`、`checkpoint`、`adapter_registry`、
`evidence`、`observation`、`asset`、`gap`。migration 2 为动作恢复增加
`attempt`、`started_at`、`completed_at`、`error_code`。
Migration 3 增加 `subtask`、`agent_lease` 和 `subtask_result`，用于租约、心跳、
超时恢复和重复结果提交保护。
Migration 4 扩展 `action` 的执行上下文和外部状态字段，并增加 `adapter_call`
审计表，用于统一执行器记录脱敏输入、适配器版本、request id、错误分类和完成时间。
Migration 5 增加 `budget` 表，支持按任务和预算类型进行事务性预留、提交和释放，
用于并发执行时避免成本超卖。
Migration 6 增加 `fofa_query_batch` 和 `fofa_cursor`，持久化 FOFA 查询指纹、
拆分批次、分页 token、request id、成本、结果数量和独立失败状态。
R2 收尾扩展批次的 `estimated_fpoints` 与 `consumed_fpoints`，并将 FOFA 搜索、
stats、host 和 account 操作的脱敏结果写入统一 evidence/observation 索引。

后续批次增加
`plan`、`asset`、`evidence`、`observation`、`gap`、`budget`、`adapter_registry`、
`review_item`、`subtask` 和 `agent_lease`，并通过迁移演进。
