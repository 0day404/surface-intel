# Operations

`init` 初始化任务目录和 SQLite；`create` 持久化任务与范围快照；`status` 只读任务；
`validate` 只读检查授权确认。P0 的未确认范围只能进入 `awaiting_scope`，不得触发外部调用。
