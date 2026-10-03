# Schema

当前 schema version 为 `1.0`，SQLite migration version 为 `1`。

P0 实体：`task`、`scope_snapshot`、`action`、`event`、`checkpoint`。后续批次增加
`plan`、`asset`、`evidence`、`observation`、`gap`、`budget`、`adapter_registry`、
`review_item`、`subtask` 和 `agent_lease`，并通过迁移演进。
