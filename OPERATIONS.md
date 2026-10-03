# Operations

`init` 初始化任务目录和 SQLite；`create` 持久化任务与范围快照；`status` 只读任务；
`validate` 只读检查授权确认。P0 的未确认范围只能进入 `awaiting_scope`，不得触发外部调用。

P5 命令：`checkpoint` 创建可恢复检查点，`recover` 将无终态的 `started` 动作标记为
`unknown`（外部终态不确定时禁止盲目重放），`pause`、`resume`、`cancel` 只改变持久化
任务状态并写入事件日志。报告从 SQLite、观察和证据索引重建。
