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
`whois_text_unverified` 许可标记）；重复执行是合法重新观察。

R4.3 放开 ASN 直查：`rdap-lookup --kind asn` 在两种依据下放行——(a) 范围文档
显式授权（`authorized_asns`）；(b) R4 归并绑定：任务中存在 `rdap_asn` 关系且
来源资产 scope confirmed（即该 ASN 由授权目标自身的注册记录揭示）。无依据时
拒绝（`asn_binding_not_found`）。绑定路径下动作的 scope 检查目标为 in-scope
锚点资产，ASN 经 `lookup_target` 携带，两者都进入 `adapter_call` 审计；观察
`target` 记录被查 ASN、`scope_anchor` 记录锚点。ASN 数据仍只能产生 candidate
归属，组织名/ASN 相同绝不确认资产。

R5 执行强化（执行器 + 调度）：`run --mode serial|hybrid|delegated` 现在映射
真实调度策略——serial 顺序执行、hybrid 以并发信号量（默认 2）并行被动动作、
delegated 按适配器分组创建租约型 subtask 行再逐组执行。执行器新增三级运行
门控：`RateLimiter`（每适配器与每目标独立最小间隔时间线，测试注入时钟）、
`action_timeout`（超时先取消；取消失败标记外部终态 `unknown`，绝不假装
知道）、`max_consecutive_errors`（连续失败达到阈值置 stop 并停止调度）。
暂停/取消/连续错误在动作之间生效，不产生新的分页或探测调用。四级预算
`BudgetLedger`（task/phase/target/adapter，budget 表 kind 前缀
`level:key`）在执行器中原子多级预留——任一级无余量则整组回滚；已执行动作
（含 failed/timed_out）提交消耗事实，不伪造退回。scope 变化时
`freeze_stale_actions` 冻结旧版本未完结动作（cancelled+scope_version_frozen），
已完成结果留作历史快照。

R6 真实观察入口（均要求 `--real --confirm-real`、范围开启 active_testing、
R4 架构门控与 R5 预算/停止在执行器层生效）：
`http-observe <task-id> --url <url> [--method GET|HEAD]`——低影响 GET/HEAD，
逐跳重定向授权（范围外 Location 阻断不联系）、私网/回连连接前拒绝、
body ≤256KB 仅存摘要、响应头白名单、TLS 证书摘要、200/404/403/429/超时/
TLS 错误分别建模；架构阻断集（CDN/WAF/LB/云/第三方 candidate/likely）
生成高优先级 review item 且零流量。
`port-check <task-id> --target <host> --port <n>`——端口必须显式列入范围
`allowed_ports`（空清单授权为零），无扫描原语；开放观察恒 candidate。
`browser-observe <task-id> --url <url> [--max-requests n]`——外部 Playwright
MCP（`npx -y @playwright/mcp@latest --stdio`）公开页观察；浏览器范围对象
限制落点（范围外 origin 记 third_party_origins→needs_review 不跟随），
网络摘要丢弃 headers/cookies/post_data；登录/状态变更/下载在适配器契约
拒绝；未配置 node/npx 时健康检查给出显式 not_configured 原因。

R4.1 增加离线归并入口：`surface-intel merge <task-id> --task-root .\task` 读取
任务全部 observation（R4.4 起覆盖 dns_resolver/ct_log/rdap_lookup/fofa_mcp，
未知来源计入 skipped），幂等地生成/更新统一资产、关系和冲突行，并执行架构
识别（architecture_status + 逐信号证据）。归并不发起任何网络请求、不修改任务
状态，可重复执行：first_seen 保留、last_seen 推进、历史值并存、属性冲突写
`asset_conflict`（unresolved）而非择值；架构识别只产出 candidate/likely/
third_party，confirmed_* 留给 R6 主动验证。R4.6 起关系带生命周期
（current/disappeared，last_transition 记 new/disappeared/reappeared——对应
新增/删除/重现与临时变化），置信度按证据年龄确定性衰减（scope 锚点除外）。
`report` 命令的 JSON 含 `asset_relations`/`asset_conflicts`/`merge_summary`，
XLSX 含 `资产关系`/`归并冲突` 工作表。
