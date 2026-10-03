# Security

默认 `passive_only`，未确认授权时禁止目标侧请求。凭据、Cookie、Token、
Authorization 和密码不得进入普通日志、SQLite 结果字段或报告。未知授权、第三方、
共享基础设施和未确认源站必须暂停并标记待复核。

适配器健康状态必须明确区分 `available`、`degraded`、`not_configured`、
`unsupported` 和 `failed`。能力不可用时必须阻止对应真实外部调用或将结果标记为
降级状态，不能用离线 Mock 结果伪装真实来源已完成。
