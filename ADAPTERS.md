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

R3.2 补充：新增 ctlogs.dev 备用 CT 源（`CrtlogsDevClient`，
`SURFACE_INTEL_CT_SOURCE=ctlogs` 切换，crt.sh 仍为默认）。ctlogs.dev 搜索行只含
匹配名（无 SAN 列表，仅 `san_count`），因此 `sans` 初始只有匹配名且
`san_completeness=match_only`；默认对前 5 条（`SURFACE_INTEL_CT_DETAIL_LIMIT`
可调）best-effort 调用证书 detail 端点回填完整 `san_dns`、subject CN、完整
issuer DN 和 SCT 时间戳（first_seen/last_seen），回填失败静默保留 match-only
行。每次搜索消耗 1 + min(detail_limit, 行数) 个请求；匿名限额 100 请求/小时，
`SURFACE_INTEL_CT_API_KEY` 提供 Bearer key。两个真实 client 都要求可识别
User-Agent（通用 python-urllib UA 被 ctlogs.dev 403）。跨源共性：ctlogs.dev 的
contains 类匹配噪声（如 `notexample.org` 匹配 `example.com` 查询）与 crt.sh
一样会经匹配名分类升级为 `needs_review`，不产生 `confirmed`。

R4.2 架构识别（`architecture.py`，随 `merge` 命令执行）：基于静态厂商签名
（`VENDOR_SUFFIXES` 域名后缀：cloudflare/akamai/cloudfront/fastly/cdn77/
azure-cdn/imperva/aws-elb/azure-traffic-manager/aws/azure/google-cloud/
route53/ultradns/outlook/google-workspace 等；`ORG_KEYWORDS` 组织与证书签发
者关键词）对合并资产分类。信号来源：DNS CNAME/NS 目标后缀、RDAP 组织/注册商
关键词、CT 证书签发者关键词。单一信号 → `{category}_candidate`，同类两份
独立信号 → `{category}_likely`（类别优先级 cdn>waf>lb>cloud_hosting>dns>
email）；范围外关联资产 → `third_party`。**绝不写入** confirmed_* /
origin_candidate（R6 主动阶段与人工复核专用）；分类不改变 scope_status；
每条证据携带 observation_id 可溯源，重复执行字节级一致。签名表为冻结的启发
式种子，按需扩充新签名而非放宽匹配规则。

R3.2 补充（自动故障转移）：`SURFACE_INTEL_CT_SOURCE` 默认 `auto`——
`FailoverCtClient` 以 crt.sh 为主源、ctlogs.dev 为备源。仅当主源返回基础设施
失败（rate_limited/timeout/http_error/invalid_response/transport 死亡）时才
转移；定案结果（`observed`/`empty`）永远可信、不重查。转移后的 outcome 携带
显式 `failover` 标记（`from`/`reason`/`primary_error`/`fallback`），adapter
透传进 observation（含失败观察），evidence 来源始终可审计——不存在静默换源。
`crtsh`/`ctlogs` 显式取值可锁定单一源（无转移）。转移在最坏情况下使单动作
外部请求耗时翻倍（两源各一个超时窗口）；预算仍按动作记账。

R3.3 增加 `RdapAdapter`（`rdap_lookup` 1.0，`passive_only`，注入式 lookup
client）。目标种类白名单 domain/ip/asn（`TARGET_KINDS`）；观察保存注册组织、
registrar、abuse 联系（仅 RDAP `abuse` 角色 vCard email）、事件时间
（registration/expiration/last_changed）、CIDR、地址/ASN 区间、country、状态和
nameservers。observed/not_found 是定案结果；rate_limited/http_error/unsupported/
invalid_response/timeout 属基础设施失败。真实 client
（`rdap_client.SequentialLookupClient`）先走 `RdapOrgClient`（rdap.org bootstrap，
`SURFACE_INTEL_RDAP_ENDPOINT` 可覆盖；404→not_found、429→rate_limited），域名
404 且 WHOIS 能确认已注册时回退 `WhoisFallbackClient`（端口 43、IANA 首查、至多
一次 referral；只提取白名单字段，绝不挖掘联系人地址，原始文本不落盘），回退记录
强制携带 `data_quality=whois_fallback`。runner 对注册数据一律 `candidate` 归属：
组织名或 ASN 相同绝不确认资产；ASN 直查在 R4 提供绑定依据前被
`asn_scope_binding_deferred` 拒绝。
