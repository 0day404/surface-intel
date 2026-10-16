# FOFA MCP

一个面向 Agent 的 FOFA MCP 服务。项目融合结构化查询、多账号限流切换、
自定义字段、传统分页，以及游标分页、统计、Host 详情和账户信息能力。

## 安装与启动

在项目根目录执行：

```powershell
uv sync
Copy-Item .env.example .env
# 编辑 .env，至少填写 FOFA_KEY
uv run fofa-mcp
```

也可以使用：

```powershell
uv run python -m fofa_mcp
```

两个入口都调用同一个 `fofa_mcp.server.main()`，启动行为和退出码保持一致。
项目内的 PowerShell、CMD 和 shell 脚本只调用主入口，不复制服务启动逻辑。

`.env` 默认位于本项目根目录，已被 `.gitignore` 排除。服务不会把 Key 写入
日志、错误、工具返回值或请求诊断信息。

如需使用其他凭据文件，可设置 `FOFA_ENV_FILE`。绝对路径按原路径读取，
相对路径相对于项目根目录解析，而不是相对于 Agent 当前工作目录。进程环境
变量优先于 `.env` 文件中的同名配置。

Agent 安装时应在本项目根目录执行 `uv sync`，并在启动前检查根目录
`.env` 是否存在及 `FOFA_KEY` 是否已填写。项目不读取其他 Agent 的配置文件，
也不会自动修改 Agent 的配置。

使用 `scripts/start.ps1`、`scripts/start.cmd` 或 `scripts/start.sh` 时，
脚本会先检查 `uv` 是否在 `PATH` 中；缺失时会返回非零退出码并提示安装及
`uv sync`。依赖由 `uv run` 检查，凭据缺失时只报告配置文件路径和修复命令，
不会输出 `.env` 内容。

启动脚本都会从脚本自身位置解析项目根目录，因此项目路径包含空格时仍可启动，
并且从其他当前工作目录调用也不会改变 `.env` 和依赖的定位。PowerShell、CMD
和 POSIX shell 脚本最终都调用 `uv run fofa-mcp`。

`fofa_health` 会检查本地配置文件、已配置账号数量、账号实时状态（可用/冷却/
最近错误，`accounts_state`）、预算限制和版本信息，不会调用 FOFA 远端 API。
错误响应会返回稳定的错误类型及 `retryable` 标识；malformed JSON、非对象响应
和字段数量错位不会被伪装成空结果。

诊断日志只写入 stderr，包含工具名、`request_id`、`task_id`、查询指纹、
账号标签、耗时和成本字段。日志不会记录完整查询、Key、认证 URL、Banner、
Header 或证书内容；stdout 保留给 MCP 协议。

## 版本与测试

`fofa_health` 返回四类版本：服务版本、工具 schema 版本、结果 schema 版本和
配置 schema 版本。工具参数有 schema 快照测试；FOFA API 的正常、空结果、限流、
认证失败、配额不足、字段错误和 malformed JSON 均使用本地 fixtures 测试，不依赖
真实 FOFA 请求。启动 smoke test 覆盖凭据缺失、备用 `.env` 和非项目工作目录。

## MCP 工具

- `fofa_search`：使用完整 FOFA DSL 进行普通搜索，支持字段和页码。
- `fofa_search_structured`：使用结构化条件生成 FOFA 查询。
- `fofa_search_next`：使用 FOFA 游标继续获取结果。
- `fofa_stats`：获取字段聚合统计。
- `fofa_host`：查询 IP 或域名的 Host 聚合信息。
- `fofa_account_info`：查询当前账号状态和配额。
- `fofa_health`：只检查本地配置，不调用远端 API。

服务只查询 FOFA 已有数据，不执行端口扫描、漏洞利用、爆破或主动探测。

普通搜索字段和 `fofa_stats` 聚合字段使用不同的校验规则。统计默认使用
`title`、`country`；不适用于聚合接口的原始内容字段（例如 `banner`）会被明确拒绝，
避免把搜索字段误当成统计维度。`fofa_host` 的目标继续使用独立 Host 输入校验。
搜索结果字段与返回数组长度不一致时会返回响应结构错误，不会静默截断或错位。
`fofa_stats` 返回统一的 `field`、`value`、`count` 数据项；`fofa_host` 支持
IPv4、IPv6 和主机名，并对 `detail` 响应施加大小上限。`full=true` 仅适用于
游标查询，默认关闭，显式启用时会返回风险和成本提示。

## Agent 调用顺序

```text
fofa_health
  └─ 仅在启动或配置故障时调用

fofa_account_info
  └─ 用户要求配额、开始大任务或发生配额错误时调用

fofa_search / fofa_search_structured
  ├─ has_more=true 且 next_token 存在 -> fofa_search_next
  ├─ 需要端口/国家/产品等分布 -> fofa_stats
  └─ 发现重点 IP/域名 -> fofa_host
```

`fofa_search` 与 `fofa_search_structured` 是二选一。搜索工具每次只取一页；
只要仍有 `next_token` 且任务预算允许，编排层应继续分页，不得把第一页
当成完整收集。`fofa_stats` 和 `fofa_host` 不应在每次搜索后自动调用。

游标续查规则：`fofa_search` / `fofa_search_structured` 返回的 `next_token`
已在本服务进程内登记，可直接交给 `fofa_search_next` 并传
`cursor_page = 上一页 page + 1`。token 绑定 `(task_id, query, fields, full)`，
四者任一变化都会被拒绝；token 只存在于服务进程内存，服务重启后旧 token
返回 `pagination_error`，应从头重新搜索。`next` 游标为空（`has_more=false`）
时不登记，续查应停止。

每次工具调用都会生成或接受安全格式的 `request_id`；结果和错误响应同时返回
`request_id` 与 `task_id`。`fofa_health` 不属于任务调用，因此其 `task_id` 为
`null`。关联标识不会包含 Key 或认证 URL。

## 安全与成本边界

- `full=true` 只用于明确请求的游标查询，可能扩大查询范围和消耗。
- `size`、游标页数、查询长度和统计数量均有本地上限。
- 多账号只用于限流和可用性容灾，不绕过 FOFA 权限或平台限制。
- 当前账号网络重试耗尽后会切换到下一个已配置账号；结果中的
  `account.tried` 记录尝试过的账号标签。
- 配额/F 点耗尽类错误立即停止重试并切换账号；所有账号都失败时抛出
  最后一个真实错误（保留原始原因），不替换成笼统的失败消息。
- 可重试错误（限流/网络）按指数退避重试，尊重 `Retry-After` 头；
  可用 `FOFA_RETRY_BACKOFF_SEC`（默认 0.5，0-30）与
  `FOFA_MAX_CALL_SECONDS`（默认 90，5-600）调节，单次调用总时长受后者
  硬性约束。
- 账户信息只返回必要状态和配额数据，Key、Token、密码等敏感字段会被移除，
  邮箱等标识会脱敏。
- 日志写入 stderr，stdout 仅用于 MCP 协议。

## 设计约束

- 默认使用 stdio MCP，适配支持标准 MCP 的 Agent。
- 默认单页返回 100 条；单页大小不代表任务总量。FOFA 实际结果可能少于
  请求的 100 条；只要存在后续结果，
  编排层应继续分页，直到结果耗尽或达到明确的安全预算。
- `FOFA_MAX_PAGE_SIZE` 只限制单次响应；`FOFA_MAX_TOTAL_RESULTS`、
  `FOFA_MAX_REQUESTS_PER_SESSION` 和 `FOFA_MAX_ESTIMATED_FPOINTS`
  限制整个服务进程的累计调用规模。`fofa_account_info` 是免费调用，
  不消耗请求预算。
- HTTP 连接池在多次调用间复用，生命周期跟随服务进程（stdio 进程退出时
  由操作系统回收）；查询/游标/预算的进程内跟踪表有容量上限，超限淘汰
  最旧条目，内存不随调用量无界增长。
- 多账号只用于可用性和限流容灾，不绕过 FOFA 权限或平台限制。
- 日志写入 stderr，stdout 仅用于 MCP 协议。
