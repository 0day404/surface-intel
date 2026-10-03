# Adapters

MockPassiveAdapter 和 MockFofaAdapter 用于离线 fixture；FofaMcpAdapter 只接受注入的
MCP 调用函数，不包含 FOFA 实现。FOFA 必须通过独立适配器调用
`E:\Work\Copilot\fofa-mcp`；Playwright 使用外部命令
`npx -y @playwright/mcp@latest`，不复制源码、依赖、缓存或凭据。

P4 增加 `CapabilityRegistry`，每个适配器必须声明能力、所需主动等级、成本、分页/
恢复/取消能力、外部副作用、重试和敏感数据策略。`SafeHttpAdapter` 仅允许授权上下文
中的 GET 观察；`SafePortAdapter` 仅允许授权端口验证，拒绝端口范围扫描。

适配器 manifest 还声明能力状态：`available`、`degraded`、`not_configured`、
`unsupported` 或 `failed`。Registry 会拒绝无效 manifest，并在非 `available` 状态
下阻止能力检查通过。Mock 适配器只表示离线 fixture 能力，不代表对应真实数据源可用。

P6 增加 `PlaywrightAdapter`。它只封装外部 MCP 调用，启动命令为
`npx -y @playwright/mcp@latest`；未配置时返回 `unknown/not_configured`。只允许授权
域名的公开页面观察，自动阻止第三方域名、登录、状态变更和下载。

R3.1 增加 `DnsAdapter`（`dns_resolver` 1.0，`passive_only`，注入式 resolver client）。
记录类型白名单为 A/AAAA/CNAME/NS/MX/TXT/SOA；AXFR/ANY 等类型在 validate 阶段被
拒绝。每个观察记录 TTL、resolver、解析耗时、resolved_at 和 DNSSEC AD 位
（`dnssec=True` 仅表示上游 resolver 置位 AD，本项目不做本地验证；未置位记 `None`）。
NXDOMAIN/empty 是定案的否定观察（动作 `completed`）；servfail/refused/rate_limited/
timeout 属基础设施失败（动作 `failed`/`timed_out`，部分成功时聚合为 `partial`）。
真实 client 是 `dns_client.DnspythonResolverClient`：绝对名称查询（跳过 search 列表）、
per-query timeout、EDNS0；nameserver 可用 `SURFACE_INTEL_DNS_NAMESERVERS` 覆盖。
运行经 `AdapterExecutor` 逐类型执行，写入 `adapter_call` 审计与预算；解析出的
IP/CNAME/NS/MX 值只有自身在授权范围内才是 `confirmed`，否则保持 `candidate`，
绝不递归解析第三方目标。

R3.2 增加 `CertificateTransparencyAdapter`（`ct_log` 1.0，`passive_only`，注入式
CT log client）。每次查询保存证书的 CN、SAN、issuer、serial、validity
（not_before/not_after）、log source、first_seen/last_seen 和 crt.sh log id。
SAN 只产生候选关系：所有名称都在授权范围内时观察仍为 `candidate`，任何第三方或
范围外域名（含 CN）把观察升级为 `needs_review`；通配符 SAN 按去掉 `*.` 的基础域
判定。CT 观察绝不创建 `confirmed` 资产或 `confirmed_direct` 关系。observed/empty
是定案结果（动作 `completed`）；rate_limited/http_error/timeout 属基础设施失败
（动作 `failed`/`timed_out`）。真实 client 是 `ct_client.CrtShClient`（crt.sh JSON
API，`SURFACE_INTEL_CT_ENDPOINT` 可覆盖端点）；URL 参数经 percent-encoding 防注入，
原始响应体不落盘不进日志，只持久化规范化证书行。health 只报告端点配置，不发起
网络请求。
