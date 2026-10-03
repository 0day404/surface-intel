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

R3.1 增加被动 DNS 入口：`dns-health --real` 只报告 resolver 配置，不查询目标；
`dns-lookup` 要求 `--real --confirm-real`、已确认范围且域名在范围内。运行把任务
置为 `collecting_passive`，逐记录类型经统一执行器执行（`adapter_call` 审计 +
`dns_resolver` 预算），结果以 `completed`/`partial`/`failed` 收口。观察写入现有
evidence/observation 表，带 `source_quality`、`license_status` 和
`related_scope_status`；DNS 重复执行是合法的重新观察（新时间戳与 TTL），不按
幂等键跳过。

R3.2 增加被动 CT 入口：`ct-health --real` 只报告端点配置，不发起网络请求；
`ct-search` 要求 `--real --confirm-real`、已确认范围且域名在范围内。运行把任务
置为 `collecting_passive`，单动作经统一执行器执行（`adapter_call` 审计 +
`ct_log` 预算），结果以 `completed`/`failed`/`blocked` 收口。crt.sh 对大域名可能
返回大量证书行：整个响应规范化为一条 observation（证书按行保存 CN/SAN/issuer/
serial/validity/first_seen/last_seen），原始响应体不落盘。默认端点
`https://crt.sh/`，可用 `SURFACE_INTEL_CT_ENDPOINT` 覆盖；请求超时默认 30 秒
（crt.sh 慢查询常见），`rate_limited`/`http_error` 属基础设施失败，重跑是合法
重新观察。
