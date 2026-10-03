# Operations

`init` 初始化任务目录和 SQLite；`create` 持久化任务与范围快照；`status` 只读任务；
`validate` 只读检查授权确认。P0 的未确认范围只能进入 `awaiting_scope`，不得触发外部调用。

P5 命令：`checkpoint` 创建可恢复检查点，`recover` 将无终态的 `started` 动作标记为
`unknown`（外部终态不确定时禁止盲目重放），`pause`、`resume`、`cancel` 只改变持久化
任务状态并写入事件日志。报告从 SQLite、观察和证据索引重建。

委派任务通过 `subtask` 和 `agent_lease` 持久化。租约过期会将运行中子任务标记为
`timed_out`；心跳只能延长仍未过期的活动租约。结果使用 `idempotency_key`，重复提交
返回已有结果，不重复写入。子 Agent 不能直接修改全局资产、预算或任务完成状态。

R0 能力预检区分 `available`、`degraded`、`not_configured`、`unsupported` 和
`failed`。状态不是 `available` 的适配器不能被当作真实成功来源；离线 Mock 必须在
报告和能力矩阵中单独标识。
