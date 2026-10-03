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
重新观察。源策略由 `SURFACE_INTEL_CT_SOURCE` 控制：`auto`（默认，crt.sh 主源
+ ctlogs.dev 故障转移，转移后观察携带 `failover` 标记）、`crtsh`/`ctlogs`
（锁定单源，无转移）。ctlogs.dev 匿名 100 请求/小时，
`SURFACE_INTEL_CT_API_KEY`（免费 key）提高限额，`SURFACE_INTEL_CT_DETAIL_LIMIT`
（默认 5）控制每搜索的 SAN 回填请求数——SAN 回填消耗额外请求，match-only 行的
`san_completeness` 字段标明数据完整度。auto 模式最坏情况单动作跨两源各一个
超时窗口，属预期行为。

R3.3 增加被动注册数据入口：`rdap-health --real` 只报告端点配置，不发起网络请求；
`rdap-lookup` 要求 `--real --confirm-real`、已确认范围且目标在范围内（IP 需在
授权 IP 列表或 CIDR 内）。运行把任务置为 `collecting_passive`，单动作经统一执行
器执行（`adapter_call` 审计 + `rdap_lookup` 预算），结果以
`completed`/`failed`/`blocked` 收口。默认端点 `https://rdap.org/`，可用
`SURFACE_INTEL_RDAP_ENDPOINT` 覆盖；TLD 无 RDAP 时自动回退 WHOIS（端口 43，
`whois_text_unverified` 许可标记）。ASN 直查被 `asn_scope_binding_deferred` 拒绝
（R4 归并层提供绑定依据后放开）；重复执行是合法重新观察。

R4.1 增加离线归并入口：`surface-intel merge <task-id> --task-root .\task` 读取
任务全部 observation（当前覆盖 dns_resolver/ct_log/rdap_lookup，其他来源计入
skipped），幂等地生成/更新统一资产、关系和冲突行，并执行架构识别
（architecture_status + 逐信号证据）。归并不发起任何网络请求、不修改任务
状态，可重复执行：first_seen 保留、last_seen 推进、历史值并存、属性冲突写
`asset_conflict`（unresolved）而非择值；架构识别只产出 candidate/likely/
third_party，confirmed_* 留给 R6 主动验证。
