# surface-intel Agent 接管提示词

你现在接管的是一个长期工程项目。请把本文作为当前任务的最高优先级上下文，
并在开始修改前阅读以下文件：

> 路径约定：`<REPO_ROOT>` = 本仓库根（本文件所在仓库的顶层目录）；
> `<FOFA_MCP_ROOT>` = fofa-mcp 子项目根（已 vendored 于本仓库
> `<REPO_ROOT>/mcp/fofa-mcp/`，保持独立版本与测试；解析顺序：env
> `SURFACE_INTEL_FOFA_ROOT` > 内置 `<REPO_ROOT>/mcp/fofa-mcp/` > 历史路径
> `E:\Work\Copilot\fofa-mcp`）。本仓库可整体迁移，位置不固定。

```text
<REPO_ROOT>\README.md
<REPO_ROOT>\ARCHITECTURE.md
<REPO_ROOT>\SCHEMA.md
<REPO_ROOT>\ADAPTERS.md
<REPO_ROOT>\OPERATIONS.md
<REPO_ROOT>\SECURITY.md
<REPO_ROOT>\docs\REAL_COLLECTION_TASK_PLAN.md
<REPO_ROOT>\docs\checkpoints\
```

## 1. 项目身份和最终目标

项目路径：

```text
<REPO_ROOT>
```

这是一个独立的 `surface-intel` Skill/Coordinator，用于在明确授权范围内执行
可追溯、可恢复、可审计的互联网攻击面和 Web 架构信息收集。

独立 FOFA MCP 项目：

```text
<FOFA_MCP_ROOT>
```

`surface-intel` 只能通过 Adapter 和 MCP stdio 客户端调用 FOFA。fofa-mcp 虽然
vendored 在本仓库 `mcp/fofa-mcp/`，仍按独立子项目对待：未经批准不得重写或修改，
也不能把跨源编排逻辑写入该项目。

Playwright MCP 是外部组件，命令为：

```powershell
npx -y @playwright/mcp@latest
```

不得把其源码、`node_modules`、浏览器缓存、Cookie、Token 或凭据写入本项目。

最终目标是：接收模糊目标和授权范围，标准化目标，创建持久化任务，执行预检和
安全门控，调用多个被动/低影响适配器，保存 observation/evidence，归并资产，
识别架构，生成缺口和 JSON/XLSX 报告，并支持暂停、恢复、重试、取消、崩溃恢复
和从 SQLite/checkpoint 重建结果。

## 2. 当前 Git 和阶段状态

当前主线最近提交：

```text
72f83e3 fix: 18 项实测缺陷修复批次（D 包前的最后基线；后续提交见 git log）
```

此前重要提交：

```text
5be4087 feat: add controlled real FOFA stdio queries
71c7703 feat: close FOFA query evidence loop
b417e8b fix: complete authorized FOFA smoke test path
```

接管时先执行：

```powershell
Set-Location <REPO_ROOT>
rtk git status --short
rtk uv run pytest -q
```

当前工作树在最近一次收口时是干净的。不要回滚、覆盖或清理其他 Agent 的改动；
如果发现未提交修改，先阅读并整合，除非存在明确冲突才暂停询问。

阶段状态：

| 阶段 | 状态 | 说明 |
|---|---|---|
| P0-P6 | completed | 离线骨架、安全门控、恢复、委派和报告已实现 |
| R0 | completed | 能力状态契约和覆盖基线已冻结 |
| R1 | completed | 统一执行器、scope/time/budget 门控已接入 |
| R2 | accepted (2026-10-04) | 真实 FOFA 闭环 + R3-R7 验收通过后由 bounded 升级（见 R7 最终验收 checkpoint） |
| R3 | completed (2026-10-03) | DNS/CT/RDAP 被动多源闭环（含 ctlogs.dev 备源与自动故障转移） |
| R4 | completed (2026-10-03/04) | 资产归并、架构识别、来源质量（migration 8-11） |
| R5 | completed (2026-10-04) | 四级预算、速率/并发、stop token、scope 冻结、真实调度模式 |
| R6 | completed (2026-10-04) | Safe HTTP/Port/Playwright 真实观察（架构门控+review item） |
| R7 | completed (2026-10-04) | 报告十六表、baseline 监控差异、case 验收与 CLI 全链路 |

R2 升级依据（原 `bounded` 升级条件已满足）：R3-R7 的限制、覆盖、预算、归并
和主动观察能力已全部实现并通过 420 项五类测试；2026-10-04 在授权目标
`www.example.com` 上完成真实全链 smoke（FOFA 查询/拆批 + 归并 +
架构门控 + Safe HTTP/Port 主动观察），期间发现并修复 3+1 个真实缺陷。
真实多页分页与更大范围授权的持续监控仍以单次授权任务为单位执行。

## 3. 已完成的架构和能力

### 3.1 持久化和状态

SQLite 当前 migration/schema version 为 `12`（`state/database.py` `SCHEMA_VERSION = 12`），包含：

```text
task
scope_snapshot
plan/action
event
checkpoint
asset
evidence
observation
gap
budget
adapter_registry
adapter_call
subtask
agent_lease
subtask_result
fofa_query_batch
fofa_cursor
fofa_query_plan
```

状态必须使用项目已有枚举，不得新造相近字符串：

- task：`created`、`preflight`、`awaiting_scope`、`planned`、
  `collecting_passive`、`collecting_fofa`、`observing_browser`、
  `analyzing_architecture`、`probing_authorized`、`correlating`、`exporting`、
  `completed`、`paused`、`blocked`、`cancelled`、`needs_review`、`partial`、
  `failed`
- action：`planned`、`started`、`completed`、`failed`、`blocked`、`cancelled`、
  `needs_review`、`timed_out`、`unknown`、`partial`
- capability：`available`、`degraded`、`not_configured`、`unsupported`、
  `failed`

`started` 且没有外部终态的动作不得直接重放，必须恢复为 `unknown` 或
`needs_review`。已完成动作按 idempotency key 跳过。结果必须通过持久化引用
恢复，不能依赖对话上下文。

### 3.2 统一 Adapter

Adapter 是薄边界，只返回结构化结果和 evidence 引用，不直接修改全局资产、
预算、范围或任务完成状态。核心契约位于：

```text
src\surface_intel\adapters\base.py
```

FOFA Adapter 位于：

```text
src\surface_intel\adapters\fofa.py
```

FOFA 真实 transport 位于：

```text
src\surface_intel\fofa_client.py
```

默认启动独立 MCP：

```text
uv run fofa-mcp
cwd = <FOFA_MCP_ROOT>
```

FOFA 支持的工具：

```text
fofa_health
fofa_account_info
fofa_search
fofa_search_structured
fofa_search_next
fofa_stats
fofa_host
```

真实 MCP 响应使用 `ok/data/pagination/cost` envelope。适配器已经处理：

- `data` 到 observations；
- `pagination.next_token`；
- `collection.has_more`；
- `cost.consumed_fpoint`；
- `cost.required_fpoints`；
- 错误对象的 `message/type`；
- `confirm=true` 和 `task_id`；
- stdio initialize、initialized、tools/call、超时和关闭。

### 3.3 R2 FOFA runner

核心文件：

```text
src\surface_intel\fofa_runner.py
src\surface_intel\fofa_queries.py
src\surface_intel\state\fofa.py
```

能力：

- 查询指纹；
- 超长查询按未加引号的 `&&` 拆分；
- 引号内 `&&` 不拆分；
- 单条件超过限制时拒绝；
- parent query、batch index、split reason；
- `fofa_query_plan`；
- 初始页使用 search；
- 有 token 时使用 search_next；
- cursor 持久化；
- 页级 request id；
- 单批失败隔离；
- evidence/observation 脱敏持久化；
- stats/host/account 仅作为 evidence/observation，不直接创建 asset；
- estimated/consumed F point 记录；
- 已完成批次幂等跳过。

报告位于：

```text
src\surface_intel\reporting\export.py
```

JSON 是主结果，XLSX 是可重建副本。报告已经包含 FOFA 计划、批次和覆盖摘要。

## 4. 已验证的真实测试

用户明确授权了：

```text
www.example.com
```

这是示例公司的授权渗透测试项目目标。本次只允许并且
只执行了 FOFA 被动查询：

```text
domain="www.example.com"
```

任务：

```text
task-9542e3d99077458c
scope_version = client-fofa-v1
batch = fofa-batch-7e50b596bb344fef
```

结果：

- 1 页；
- 2 条 observation；
- HTTP/80 和 HTTPS/443；
- evidence/observation 已写入 SQLite；
- JSON/XLSX 报告已生成；
- 未执行目标直连、端口扫描、浏览器访问或业务操作。

运行示例：

```powershell
Set-Location <REPO_ROOT>
rtk uv run surface-intel fofa-health --real
rtk uv run surface-intel fofa-search `
  --task-root .\runtime\client-fofa-20261003 `
  task-9542e3d99077458c `
  --domain www.example.com `
  --size 10 --real --confirm-real
```

真实运行必须使用明确授权的任务和域名。不能因为目标看起来属于某公司就自动
查询，也不能把用户曾经授权一个目标推断为授权整个域名、ASN、相邻网段或第三方
资源。

## 5. 不可违反的安全边界

默认禁止：

- 漏洞利用、爆破、凭据尝试；
- 认证/MFA/CAPTCHA 绕过；
- WAF/CDN 绕过；
- SSRF/RCE 回链；
- DoS/压力测试；
- ASN、相邻网段、共享云或第三方资产批量扫描；
- 未授权 APP/小程序反编译、Hook 或流量解密；
- 自动提交真实业务表单；
- 上传、支付、发信、创建、删除或修改业务数据；
- 自动使用发现的 Token、Cookie、密码、密钥或疑似凭据。

未确认的域名、IP、API、云资源、第三方域名、源站候选和身份提供商必须保持
`candidate` 或 `needs_review`，不能自动成为主动探测目标。

任何授权、归属、架构边界或外部工具终态不明确时，暂停并记录
`needs_review`/`unknown`，不能猜测放行。

只有 `confirmed_direct` 和明确授权的 `confirmed_hosted` 才能进入常规主动探测。
本项目后续应先完成 R3-R5，再扩大 R6 的真实主动观察。

## 6. MCP 修改审批规则

如果运行中确认问题来自 `<FOFA_MCP_ROOT>` 或其他外部 MCP，而不是
`surface-intel` 的调用、适配器或状态逻辑：

1. 先明确指出 MCP 项目、文件、接口和根因；
2. 区分代码缺陷、配置问题和调用方问题；
3. 不要直接修改 MCP；
4. 告知用户需要申请的具体修改、影响范围和验证方式；
5. 只有用户明确同意后，才可以修改并测试 MCP。

除非获得明确批准，不得触碰 `fofa-mcp`。vendored 副本无独立 `.git`——改动经
本仓库 git 追溯；历史基线以原仓 `E:\Work\Copilot\fofa-mcp`（冻结于 `e851ed1`）为准。

## 7. 下一步正确工作顺序

接管后不要重复实现已经完成的 R0-R2。优先进入 R3，顺序如下：

### R3.1 DNS Adapter

实现统一 Adapter 形式的被动 DNS 适配器，至少支持：

```text
A / AAAA / CNAME / NS / MX / TXT / SOA
TTL、解析时间、解析器、DNSSEC、错误、超时
```

要求：

- 只处理授权 scope 根下的目标；
- 不递归扩大到未授权域名；
- 结果写 observation/evidence；
- 记录 source quality、许可状态和时间；
- 不把 CNAME 或第三方域名自动确认为资产；
- 使用 mock/fixture 完成无网络测试；
- 真实 DNS 请求必须独立受 scope、预算、速率和超时门控。

### R3.2 CT Adapter

实现证书透明度被动适配器，保存：

```text
CN、SAN、issuer、serial、validity、log source、first_seen、last_seen
```

证书 SAN 只产生候选关系，不单独确认归属。第三方和不在 scope 的域名必须
进入 candidate/needs_review。

### R3.3 RDAP/WHOIS Adapter

实现注册组织、网络、ASN、CIDR、abuse 联系信息和查询时间的被动观察。
WHOIS fallback 必须明确数据质量和数据许可状态。不能因为组织名或 ASN 相同
就确认资产归属。

### R3.4 多源统一结果

补充统一 evidence metadata 和 source quality，不复制敏感原始响应到普通日志。
增加冲突 fixture、空结果、NXDOMAIN/SERVFAIL、超时、限流和许可不可用测试。

完成 R3 后才进入 R4：

- 资产模型扩展；
- 多源归并；
- 当前/历史分层；
- 冲突保留；
- CDN/WAF/LB/云托管/第三方架构识别；
- provenance 和 confidence。

## 8. 每个增量必须遵守的流程

每个阶段或独立增量都必须：

1. 先阅读设计文档对应章节；
2. 检查工作树和已有改动；
3. 更新 SQLite todo 或明确任务状态；
4. 做一个最小可验证改动；
5. 添加 unit/contract/integration/recovery/security 测试；
6. 执行最小测试，再执行完整测试；
7. 更新 README、SCHEMA、ADAPTERS、OPERATIONS、SECURITY 或计划文档；
8. 保存 `docs\checkpoints\` checkpoint；
9. 运行 `rtk git diff --check`；
10. 提交时保留 Co-authored-by trailer。

标准验证命令：

```powershell
Set-Location <REPO_ROOT>
rtk uv sync --extra dev
rtk uv run pytest -q
rtk git diff --check
```

Windows 路径使用反斜杠。所有 shell 命令使用 `rtk` 前缀。不要使用破坏性命令，
不要 `git reset --hard`，不要恢复或删除其他 Agent 的工作。

每个 checkpoint 至少说明：

```text
阶段
目标
已完成
新增/修改文件
数据库/Schema 变化
测试命令和结果
已知限制
下一步
是否可恢复
```

## 9. 交付判断标准

不能因为测试通过就声称真实生产能力完整。任何功能只有在以下条件同时满足时
才能标记完成：

- 输入、输出、错误和状态契约明确；
- 外部副作用经过 scope、预算、时间窗、能力和停止门控；
- 结果、证据、日志和状态可追溯到 task、scope version、source 和 adapter；
- 中断后可以恢复或明确标记 unknown；
- 正常、空结果、超时、限流、重复提交、越权和敏感字段有测试；
- 失败不会伪造成功，partial/bounded 不会伪装为 complete；
- status、validate、report 不依赖运行中的 Agent 记忆；
- 未实现能力明确标记 `unsupported`/`not_configured`；
- 真实能力与 fixture 能力在文档中严格区分。

当前最重要原则：不要偏离授权边界，不要跳过 R3-R5 直接扩大主动探测，不要
修改独立 MCP 项目，不要删除失败记录，不要把候选或历史观察直接提升为确认资产。
