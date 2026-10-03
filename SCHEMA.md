# Schema

当前 schema version 为 `1.0`，SQLite migration version 为 `2`。

实体：`task`、`scope_snapshot`、`action`、`event`、`checkpoint`、`adapter_registry`、
`evidence`、`observation`、`asset`、`gap`。migration 2 为动作恢复增加
`attempt`、`started_at`、`completed_at`、`error_code`。

后续批次增加
`plan`、`asset`、`evidence`、`observation`、`gap`、`budget`、`adapter_registry`、
`review_item`、`subtask` 和 `agent_lease`，并通过迁移演进。
