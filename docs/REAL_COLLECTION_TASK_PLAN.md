# surface-intel 真实高质量信息收集实施任务计划书

**版本**：1.0  
**日期**：2026-10-03  
**适用项目**：`E:\Work\Copilot\surface-intel`  
**依据**：`E:\Work\Copilot\RECON-ORCHESTRATOR-PLAN.md`  
**目标**：在不降低授权、安全、审计和恢复约束的前提下，把当前离线 MVP
推进为可用于明确授权目标的、可追溯且有界的真实多源信息收集系统。

## 1. 结论与交付边界

当前项目已经具备 P0-P6 的离线状态骨架、Mock 闭环、主动探测门控、
checkpoint、子任务租约和报告质量门，但 `run_offline_task()` 仍直接使用
`MockPassiveAdapter` 和 `MockFofaAdapter`。因此下一阶段的首要目标不是增加更多
Mock，而是把真实适配器接入统一 Coordinator，并用 fixture、contract、recovery
和安全测试证明真实调用不会绕过范围、预算、脱敏和恢复规则。

本计划完成后，系统应达到以下可声明能力：

1. 对已确认授权范围执行被动多源收集，并记录每一次请求、分页、失败、成本、
   证据和适配器版本。
2. 对当前观察、历史观察、候选资产、第三方资产和来源冲突进行明确分层。
3. 只有满足范围、架构、主动等级、预算、时间窗和适配器能力条件时，才执行
   低影响 HTTP/端口/浏览器观察。
4. 进程崩溃、适配器超时、外部终态不确定、租约过期或范围版本变化后，能够
   恢复、暂停或明确标记 `unknown`/`partial`，不重复产生外部副作用。
5. 从 SQLite、JSONL 和证据索引重新生成 JSON/XLSX；任何报告都不会把受限、
   失败、预算截断或未授权结果伪装成 `complete`。

本计划**不包含**漏洞利用、爆破、认证绕过、WAF/CDN 绕过、压力测试、批量
扫描、真实业务状态变更或自动使用发现的凭据。任何目标、第三方域名、源站候选、
云资源或身份提供商只要归属和授权不明确，就保持 `candidate`/`needs_review`。

## 2. 当前基线和硬性不变项

### 2.1 已有基线

- Python `>=3.11`、uv、SQLite、Pydantic、pytest、openpyxl。
- schema version 3，已有 task、scope、action、event、checkpoint、evidence、
  observation、asset、gap、subtask、agent lease、subtask result。
- `FofaMcpAdapter` 是注入式薄适配器；FOFA MCP 位于
  `E:\Work\Copilot\fofa-mcp`，不得复制或修改。
- Playwright 使用外部命令 `npx -y @playwright/mcp@latest`，项目不得保存
  `node_modules`、浏览器缓存、Cookie、Token 或凭据。
- Safe HTTP/Port、架构门控、报告质量门和离线验收测试已存在，但真实执行路径
  尚未完整接入。

### 2.2 不变约束

1. 所有外部调用必须从 `Coordinator -> Policy/State -> Adapter` 进入。
2. Adapter 只返回结构化结果和证据引用，不直接写全局资产、预算、范围或任务
   完成状态。
3. `started` 且无外部终态的动作不得自动重放，必须变成 `unknown` 或
   `needs_review`。
4. `scope_version` 必须写入 action、subtask、evidence、observation 和
   checkpoint；旧版本结果不得混入新范围。
5. 预算、速率、并发、时间窗和停止条件必须在动作执行前、分页前和重试前检查。
6. 敏感字段必须在适配器边界或提交边界脱敏；普通日志、SQLite 摘要、JSON/XLSX
   不得保存 Token、Cookie、Authorization、密码、密钥或个人数据。
7. 任何不确定授权、架构归属、外部工具终态或第三方关系都不能猜测放行。

## 3. 总体实施顺序

```text
R0 当前树收口与契约冻结
  ↓
R1 真实适配器运行时和统一执行器
  ↓
R2 FOFA 生产闭环
  ↓
R3 DNS/CT/RDAP 被动多源闭环
  ↓
R4 资产归并、架构识别和来源质量
  ↓
R5 预算/速率/并发/停止与恢复强化
  ↓
R6 Safe HTTP/Port/Playwright 真实观察
  ↓
R7 报告、监控差异和完整验收
```

R1-R3 是真实信息覆盖的基础；R4 决定结果质量；R5-R6 决定能否安全运行；
R7 决定是否可以对外交付。不得跳过 R4 直接扩大主动探测，也不得在 R5 完成前
将真实任务标记为生产可用。

## 4. 分阶段任务

## R0：收口、契约冻结和可重复基线

**目标**：把当前未提交修改、现有状态和真实能力边界固定下来，避免后续在
Mock 路径上继续扩展而掩盖生产缺口。

**状态**：已完成（2026-10-03，提交 `a193e6c`，checkpoint
`docs/checkpoints/R0-2026-10-03.md`）。

### 工作项

- [x] 提交或明确保留当前报告质量门、重复运行保护和验收矩阵修改。
- [x] 为 `run_offline_task`、`FofaMcpAdapter`、`PlaywrightAdapter`、报告导出
      和恢复代码建立当前行为快照测试。
- [x] 增加 `capability_status` 枚举：`available`、`degraded`、
      `not_configured`、`unsupported`、`failed`。
- [x] 为每个适配器冻结 manifest schema：能力、主动等级、成本、分页、恢复、
      取消、重试、敏感数据策略、外部副作用和版本。
- [x] 把 README、ADAPTERS、OPERATIONS、SCHEMA、SECURITY 中的“已实现”“仅接口”
      “Mock”“not_configured”分开陈述。
- [x] 建立 `docs/coverage-matrix.md` 的机器可校验版本，逐项映射设计文档
      S-00 至 S-18、代码入口和测试入口。

### 验收

- [x] 干净目录执行 `uv sync --extra dev && uv run pytest` 成功。
- [x] 不启动外部服务即可重现所有现有测试。
- [x] 任何文档或报告都不会声称真实 FOFA、Playwright、DNS、CT 或 RDAP 已可用。

**R0 实际验证**：`uv run pytest -q` 结果为 `28 passed`；未启动真实 FOFA
MCP、Playwright MCP，未访问真实目标，未执行主动探测，`fofa-mcp` 未修改。

## R1：真实适配器运行时和统一执行器

**目标**：消除真实适配器与离线 Coordinator 之间的断层，所有适配器都通过
持久化动作运行。

**状态**：进行中（2026-10-03，R1 第一增量）。
进入条件已满足：R0 checkpoint 已保存，能力状态契约、覆盖矩阵和离线测试基线
已冻结。

### 代码和数据任务

- [x] 新增 `ExecutionContext`，包含 task、scope snapshot、budget snapshot、
      adapter manifest、correlation id、evidence root 和 stop token。
- [x] 新增 `ActionRepository`，统一实现领取、原子启动、完成、失败、
      取消、超时、未知、重试和幂等查询。
- [x] 扩展 action 字段：`phase`、`target_ref`、`input_json`、`attempt`、
      `cost_units`、`request_id`、`next_cursor_ref`、`scope_version`、
      `external_state`、`last_error_code`、`started_at`、`completed_at`。
- [x] 新增 `adapter_call` 或等价事件表，保存 adapter 名称/版本、动作 ID、
      request id、开始/结束时间、状态、重试次数、脱敏输入摘要和错误分类。
- [x] 将 `run_offline_task` 重构为可注入 adapter registry 的通用 runner；
      Mock 只作为 registry 中的一种实现。
- [x] 所有 adapter 调用前执行 `validate`、`check_budget`、`check_time_window`、
      `is_target_in_scope`、`is_action_allowed` 和 capability check。
- [x] 外部失败分类为 `timeout`、`rate_limited`、`auth_unavailable`、
      `dependency_unavailable`、`invalid_input`、`remote_error`、
      `unknown_external_state`，禁止宽泛吞错。

### 测试

- [x] 同一 idempotency key 只能产生一次外部动作。
- [x] action 在 `started` 后崩溃恢复为 `unknown`，不得直接再次执行。
- [x] adapter 返回敏感字段时，普通日志和报告均不可见。
- [x] 预算耗尽、时间窗关闭、范围版本变化和 adapter 能力不足都在外部调用
      前阻止执行。

**R1 第一增量实际验证**：schema migration version 4、`adapter_call` 审计表、
扩展 action 字段、`ExecutionContext`、`ActionRepository` 和统一
`AdapterExecutor` 已接入离线 Coordinator。`uv run pytest -q` 结果为
`32 passed`。

**R1 收口实际验证**：统一执行器现已读取 scope payload，执行目标范围、scope
version 和时间窗口门控；新增 SQLite `budget` 表及事务性 reserve/commit/release，
并发预算超卖会被拒绝。`uv run pytest -q` 结果为 `35 passed`。

**R1 尚未完成**：重试/退避、真实 MCP transport 和完整模式调度仍待后续增量；
因此当前仍只能声明离线/受控执行器能力，不能声明真实数据源生产可用。

**R2 第一增量实际验证**：确认独立 `fofa-mcp` 的 stdio 启动入口和七个工具契约；
新增 `McpCallClient` 边界、FOFA batch/cursor 表及 repository，支持 request id、
next token、成本、结果数量和单批失败隔离。`uv run pytest -q` 结果为
`39 passed`。真实 stdio 会话、真实凭据和真实 FOFA 查询尚未运行。

**R2 真实客户端增量实际验证**：新增 `StdioJsonRpcTransport`、
`FofaStdioClient` 和 `run_fofa_query`；`fofa-health --real` 与
`fofa-search --real --confirm-real` 是显式入口，后者要求已确认任务、授权域名
且通过 batch/cursor runner。协议 fixture、分页、缺 token、恢复和 MCP 错误测试
后总计 `43 passed`。当前没有使用真实凭据或真实授权目标，因此尚未执行真实
FOFA smoke test。

**R2 收尾增量实际验证**：新增安全的超长查询拆分、`estimated_fpoints`/
`consumed_fpoints` 批次字段、FOFA 搜索结果 evidence/observation 持久化，以及
stats/host/account 操作的 evidence-only 记录。执行了
`uv run surface-intel fofa-health --real`，外部 `fofa-mcp` stdio 进程成功返回
健康配置结果；该工具不发起远程 FOFA 搜索。全套测试为 `46 passed`。

真实搜索 smoke test 已在用户明确授权的
`www.ceramtec-group.cn` 上执行。任务
`task-9542e3d99077458c` 使用 `passive_only`、`size=10` 和独立证据目录，
返回 2 条观察、1 页结果；生成 JSON/XLSX 报告。未执行目标直连、端口扫描、
浏览器观察或其他主动探测。

该 smoke test 暴露并修复了真实 MCP 响应格式适配问题：FOFA 成功结果使用
`ok/data/pagination/cost`，适配器现已正确转换为观察、分页和 F 点字段。
修复后全套测试为 `47 passed`。历史失败批次保留为 failed，不被重放或覆盖。

## R2：FOFA 生产闭环

**目标**：通过独立 FOFA MCP 适配器完成真实健康检查、查询、分页、游标、
   成本和失败批次恢复，不把 FOFA 逻辑写入 `fofa-mcp`。

**状态**：bounded（2026-10-03，真实 stdio 客户端、受控授权 smoke test、
查询计划、批次恢复和 FOFA 覆盖率报告已接入；完整生产状态仍依赖 R3-R5）。

### 2.1 MCP 契约和健康检查

- [x] 明确独立 FOFA MCP 使用 `fofa-mcp` stdio 入口，封装 transport-neutral
      `McpCallClient`/注入客户端边界；Coordinator 不直接依赖 MCP SDK 细节。
- [x] 实现真实 stdio JSON-RPC transport，支持 initialize、initialized、
      tools/call、超时、进程关闭和结构化结果解包。
- [x] 为以下工具建立 contract fixture：
      `fofa_health`、`fofa_account_info`、`fofa_search`、
      `fofa_search_structured`、`fofa_search_next`、`fofa_stats`、`fofa_host`。
- [x] 健康检查分别记录：工具存在、版本、账号可用性、数据许可、网络出口、
      认证状态和成本能力；不可用时返回 `not_configured` 或 `failed`。
- [x] 真实调用仅允许在用户明确提供授权、凭据状态通过预检且目标在任务
      scope 内；本轮 CeramTec smoke test 保持 `passive_only`，开发和无凭据
      环境继续使用 fixture。

### 2.2 查询计划和批次

- [x] 对当前已授权域名/子域和已确认资产生成 FOFA 查询计划；每条查询保存
      规范化查询、父查询指纹、来源依据和预期字段。ASN、证书 CN/SAN、组织
      等目标类型将在多源 R3/R4 资产模型接入后扩展。
- [x] 实现超长查询拆分，保留 `parent_query_id`、`batch_index`、拆分原因和
      合并顺序。
- [x] 每个批次有独立记录、恢复状态、request id、成本、
      返回字段和失败状态。
- [x] 无 `next_token` 时由 adapter 选择初始 search；有 token 时选择
      `fofa_search_next`，cursor 可持久化后恢复。
      后再继续分页。
- [x] FOFA stats 只作为统计证据，不直接写入资产清单；host 结果必须与查询
      observation/evidence 关联。

### 2.3 恢复与质量

- [x] 中断后可按 `query_fingerprint + batch_id + cursor` 恢复，不按内存页数猜测。
- [x] 已完成批次状态和失败批次独立保存；重复 cursor 写入幂等。
- [x] 当前 observation/evidence 与资产表分开保存，FOFA 结果不会直接覆盖资产
      当前状态；历史/当前资产分层留给 R4 归并层。
- [x] 报告显示 FOFA 查询计划数、批次完成/失败数、观察数和估计/实际 F 点；
      未覆盖目标类型在计划元数据和后续 gap 阶段保留。

### 验收

- 多页、空结果、无 token、token 失效、单批失败、限流、账号不可用和重启恢复
  均有 contract/recovery 测试。
- FOFA MCP 外部目录 `E:\Work\Copilot\fofa-mcp` 的文件哈希和 Git 状态在测试
  前后不变。

## R3：DNS、CT、RDAP/WHOIS 被动多源闭环

**目标**：补足 FOFA 之外的独立来源，提升覆盖率和交叉验证能力；所有新来源
都必须使用统一 Adapter 协议。

### 适配器

- [ ] `DnsAdapter`：A/AAAA/CNAME/NS/MX/TXT/SOA，记录 TTL、解析时间、解析器、
      DNSSEC/错误和超时；不得递归扩大到未授权域名。
- [ ] `CertificateTransparencyAdapter`：证书 CN/SAN、issuer、serial、validity、
      log source、首次/最后观察；域名关系先标记 candidate，不能仅凭证书确认
      资产归属。
- [ ] `RdapAdapter`：注册组织、网络、ASN、CIDR、abuse 联系信息和查询时间；
      WHOIS fallback 必须明确数据质量和许可状态。
- [ ] 所有适配器输出统一 observation、evidence metadata 和 source quality，
      不把原始敏感响应直接放入普通日志。

### 计划和限界

- [ ] 以 scope snapshot 为根，只对授权域名、明确授权 IP/CIDR 和已批准查询
      目标执行。
- [ ] DNS/CT/RDAP 的速率、并发、超时、重试和缓存进入统一预算。
- [ ] 第三方、共享云、相邻网段、证书中仅出现但未确认归属的域名进入
      `candidate`/`needs_review`。

### 验收

- 多源同值、冲突值、空结果、SERVFAIL/NXDOMAIN、限流、超时和许可不可用均有
  fixture。
- CT 的 SAN、DNS 的 CNAME、RDAP 的组织信息不能单独把 candidate 升为
  `confirmed_direct`。

## R4：资产归并、架构识别和来源质量

**目标**：把“收集到数据”提升为“高质量、可解释的资产情报”。

### 4.1 统一资产模型

- [ ] 扩展 asset：`asset_type`、domain、ip、port、protocol、host、sni、
      service、product、version、organization、sources、evidence_refs、
      confidence、scope_status、architecture_status、first_seen、last_seen、
      current_status、historical_status。
- [ ] 增加 service、certificate、relationship 或等价关联表，避免把所有信息
      压成单一 asset 行。
- [ ] 所有规范化值保留原始值引用、规范化规则版本和冲突记录。

### 4.2 归并与冲突

- [ ] 对域名、IP、端口、证书、CNAME、HTTP host/SNI 和 FOFA host 建立可解释
      的关联规则。
- [ ] 单一 favicon、标题、Server header、证书组织名、相似 HTML、产品、
      ASN 或相邻 IP 只能产生候选关系，不能单独确认归属。
- [ ] 实现证据等级、来源可靠性、时间衰减和冲突保留；冲突不能被静默覆盖。
- [ ] 实现当前/历史分层：新增、删除、变化、重现、临时变化和未知。

### 4.3 架构识别

- [ ] 识别并保存 DNS/CDN/WAF/反向代理/LB/云托管/Web/API/JS/第三方依赖关系。
- [ ] 输出 `confirmed_direct`、`confirmed_hosted`、`shared_infrastructure`、
      `third_party`、`origin_candidate`、`unknown` 及其证据引用。
- [ ] 只有 `confirmed_direct` 和明确授权的 `confirmed_hosted` 才能进入常规
      主动探测；origin_candidate 永远需要人工复核。

### 验收

- 同一资产多来源一致时置信度提升但仍可追溯。
- 来源冲突同时保留，不被最后写入值覆盖。
- CDN/第三方/共享云样例阻止主动探测并生成 review item。

## R5：预算、速率、并发、停止与恢复强化

**目标**：让长任务在真实环境中可控、可暂停、可恢复且不会无限扩张。

### 工作项

- [ ] 建立 task/phase/target/adapter 四级预算：请求数、成本单位、FOFA F 点、
      字节、并发、总时长和结果上限。
- [ ] 预算采用事务性 reservation/commit/release；动作失败或未知时保留消耗
      事实，不伪造退回。
- [ ] 实现 per-adapter/per-target 速率和并发限制，重试使用退避并受总预算限制。
- [ ] 实现目标级和任务级 stop token：人工暂停、取消、预算耗尽、时间窗关闭、
      安全门控失败、连续错误阈值。
- [ ] worker lease、action timeout、heartbeat、recovery scan 与 adapter cancel
      语义统一；取消失败必须标记外部终态未知。
- [ ] scope_version 变化时冻结旧动作，创建新计划；旧结果只作为历史快照。
- [ ] 把 `serial_mode`、`delegated_mode`、`hybrid_mode` 真正映射为调度策略，
      而不是只接受 CLI 参数。

### 验收

- 并发限制不超阈值；预算在并发竞争下不被超卖。
- 暂停后不产生新的分页/探测调用；恢复只继续安全、未完成且终态明确的动作。
- `started`、超时、取消、lease 过期和进程退出均能产生可解释恢复摘要。

## R6：Safe HTTP/Port/Playwright 真实观察

**前置条件**：R4 架构门控和 R5 预算停止必须通过；不得先接真实主动流量。

### 6.1 Safe HTTP

- [ ] 接入真实异步 transport，限制方法为 GET/HEAD（按策略配置），禁止表单、
      上传、下载、认证和状态变更。
- [ ] 记录状态码、最终 URL、重定向链、TLS 摘要、响应头白名单、内容类型、
      body 摘要、超时、429 和 TLS 错误；不保存完整敏感响应。
- [ ] 对 200、404、403、429、超时和 TLS 错误分别建模，不能只记录成功/失败。
- [ ] 自动阻止未授权 Host、重定向到第三方、私网/本地回连和架构状态不允许
      的目标。

### 6.2 Safe Port

- [ ] 只验证授权目标中明确列出的端口；禁止端口范围扫描和相邻地址扩展。
- [ ] 记录连接结果、协议猜测、超时、TLS 摘要和服务识别置信度。
- [ ] 端口开放不能自动证明资产归属或允许更高等级探测。

### 6.3 Playwright

- [ ] 记录外部 MCP 命令、实际版本、连接方式、健康检查和 `not_configured`
      原因。
- [ ] 使用浏览器范围对象限制初始域名、重定向、XHR/fetch、WebSocket/SSE、
      资源和时间预算。
- [ ] 只观察公开页面：页面状态、最终 URL、SPA 路由、网络摘要、控制台错误、
      资源失败和截图元数据。
- [ ] 阻止登录、MFA/CAPTCHA 绕过、凭据尝试、上传、下载、支付、发信、创建/
      删除/修改业务数据和自动访问新第三方域名。
- [ ] Cookie、Set-Cookie、Authorization、Token、密码和个人数据在证据写入前
      脱敏或拒绝提交。

### 验收

- 在本地 fixture HTTP 服务和注入式 MCP fixture 上验证，不以真实目标作为默认
  测试对象。
- 真实环境只能使用用户明确确认的授权目标，并在任务报告中保留授权快照和
  stop reason。

## R7：报告、监控差异和最终验收

**目标**：让报告成为可复核、可重建、可判断覆盖率的交付物。

### JSON 主结果

- [ ] 增加任务摘要、授权范围、范围版本、适配器状态、预算消耗、覆盖率、
      质量状态、停止原因、恢复摘要、review queue 和 gap list。
- [ ] 增加资产、服务、域名/证书、Web 技术栈、FOFA 批次、架构关系、浏览器
      页面/网络、证据索引、事件和快照差异。
- [ ] 每个事实必须可追溯到 observation/evidence/source/adapter version。

### XLSX

- [ ] 至少生成：摘要、范围授权、资产、服务、域名证书、Web 技术、FOFA、
      架构关系、浏览器页面、浏览器网络、证据、缺口、review queue、执行日志、
      checkpoint/recovery、快照差异。
- [ ] 防公式注入、原子写入、失败保留 JSON、记录文件路径/哈希/大小/时间/
      状态。
- [ ] XLSX 与 JSON 的资产数量、完整性状态和报告版本一致。

### 持续监控

- [ ] 以持久化 baseline 比较新增、删除、变化、重现、临时变化和未知。
- [ ] baseline 只吸收质量门通过且范围版本匹配的结果。
- [ ] 监控任务默认被动；任何主动差异验证仍需单独门控。

### 最终验收

- [ ] 完成设计文档 case-01 至 case-10 的独立 fixture。
- [ ] 完成 unit、contract、integration、recovery、security 五类测试。
- [ ] 干净目录安装并执行 CLI 全链路：
      `init -> create -> preflight -> plan -> run -> status -> report`。
- [ ] 模拟进程退出、适配器超时、FOFA 多页恢复、范围变更、租约过期、导出
      失败和重复执行。
- [ ] 对真实外部服务只做经过授权、低影响、可停止的最小 smoke test；无凭据或
      无授权时明确记录 `not_configured`，不伪造成功。

## 5. 设计文档 case 与实现映射

| Case | 必须证明的行为 | 主要验证层 |
|---|---|---|
| case-01 | 单域名、无主动授权、空被动结果仍正确结束为 bounded/partial | integration/security |
| case-02 | FOFA 多页和 next token 持久化恢复 | contract/recovery |
| case-03 | 超长查询拆批，单批失败不丢其它批次 | unit/integration |
| case-04 | CDN/第三方阻止主动探测并进入 review | security |
| case-05 | 200/404/403/429/timeout/TLS error 分类准确 | contract/integration |
| case-06 | started 无终态标记 unknown，不盲目重放 | recovery |
| case-07 | lease 过期、重复提交、partial 结果幂等 | recovery |
| case-08 | XLSX 失败不影响 JSON 主结果 | integration |
| case-09 | 敏感字段脱敏或拒绝提交 | security |
| case-10 | scope version 变化隔离旧动作和旧结果 | recovery/security |

## 6. 每个阶段的固定交付模板

每个 R 阶段完成时必须同时提交以下内容，缺一不可：

```text
阶段：
目标：
代码变化：
数据库/schema/migration：
适配器或策略变化：
测试命令：
测试结果：
外部调用状态：
证据和报告变化：
已知限制：
恢复方式：
下一阶段进入条件：
```

建议使用的最小验证命令：

```powershell
cd E:\Work\Copilot\surface-intel
uv sync --extra dev
uv run pytest
uv run surface-intel init .\var\task-root
uv run surface-intel validate <task-id>
uv run surface-intel status <task-id>
uv run surface-intel report <task-id> --format all
```

真实外部 smoke test 必须单独使用受控配置、明确授权目标和独立证据目录，
不得把凭据写进命令历史、fixture、任务 JSON、SQLite、日志或报告。

## 7. 风险、停止条件和回滚

### 主要风险

- 外部 MCP 工具名称、transport 或返回 schema 变化。
- FOFA 账号、额度、数据许可或网络出口不可用。
- DNS/CT/RDAP 数据延迟、冲突、隐私限制或服务限流。
- 浏览器 MCP 版本变化、会话失效或第三方重定向。
- 真实网络响应包含敏感数据或触发未预期副作用。
- 并发执行导致预算超卖、重复请求或外部终态不确定。

### 必须暂停的条件

- 授权范围、目标归属、主动等级或第三方关系不明确。
- 适配器健康检查失败但无法判断外部终态。
- 预算、速率、时间窗或证据存储状态无法确认。
- 发现需要登录、绕过、上传、下载、状态变更或扩大到第三方目标。
- schema 迁移、范围版本或结果引用无法事务性落盘。

### 回滚原则

- 只回滚未提交的本地配置或计划，不删除事件、失败动作、证据索引和 checkpoint。
- 外部调用已经发生时，保留真实消耗和未知终态，不能通过删除记录伪造回滚。
- 新适配器故障时可切换为 `not_configured`/`unsupported` 和离线 fixture，但
  报告必须显示能力降级。

## 8. “高质量真实收集”发布门

只有同时满足以下条件，才允许把系统描述为“支持高质量真实信息收集”：

1. R1-R7 全部完成并有代码、迁移、文档和测试证据。
2. FOFA 真实 contract 和恢复路径通过；失败批次、游标、成本和账号状态可追踪。
3. DNS、CT、RDAP 至少三个独立被动来源可运行或明确标为不可用并阻止虚假
   完整性结论。
4. 资产归并能保留来源冲突、当前/历史状态和证据链。
5. 预算、速率、并发、时间窗和 stop token 在真实执行路径生效。
6. Safe HTTP/Port/Playwright 只对授权目标执行，并能阻止第三方和状态变更。
7. 所有 case-01 至 case-10、完整测试矩阵和干净安装命令通过。
8. 报告能明确列出：已完成、`partial`、`bounded`、`failed`、`unknown`、
   未授权、人工复核、不可用适配器和最后 checkpoint。

在发布门通过前，推荐的正式能力表述是：

> `surface-intel` 支持授权边界内、可审计、可恢复的离线和受控真实信息收集；
> 真实数据覆盖率、主动观察和多源交叉验证仍受已配置适配器、授权、预算和
> 外部服务状态限制。
