# surface-intel

`surface-intel` 是一个授权范围内的攻击面情报编排器。当前已提供离线 P0-P3
最小闭环：

- `init`、`create`、`preflight`、`plan`、`run`、`status`、`validate`、`report`；
- 范围标准化、排除项优先、动作幂等、Mock 被动/FOFA 多页适配器；
- SQLite 观察/证据/资产/缺口索引，以及可重建 JSON/XLSX。
- P4 安全主动门控、能力矩阵、Safe HTTP/Port 适配器和 P5 checkpoint/崩溃恢复。
- P6 子任务租约/心跳/重复结果保护，以及外部 Playwright MCP 公开页面观察边界。

P6 的 Playwright 连接通过注入的 MCP 调用边界测试；项目不复制 Playwright 源码、
`node_modules`、浏览器缓存或凭据。

`run` 支持 `--mode serial|delegated|hybrid`；三种模式共享同一持久化状态和安全门控。
恢复时读取 checkpoint，未知外部终态不会被自动重放。

当前真实数据源仍需单独配置和契约验证。适配器能力状态明确区分
`available`、`degraded`、`not_configured`、`unsupported` 和 `failed`；Mock
适配器只用于离线测试，不能证明真实 FOFA、DNS、CT、RDAP 或 Playwright 已接通。

真实 FOFA 入口是显式受控的：

```powershell
uv run surface-intel fofa-health --real
uv run surface-intel fofa-search --task-root .\task <task-id> --domain example.com --real --confirm-real
```

查询命令要求任务范围已确认、域名在授权范围内，并通过独立
`E:\Work\Copilot\fofa-mcp` 的 stdio MCP 进程调用；凭据只由 FOFA MCP 自己从其
受控环境读取，不进入 `surface-intel` 参数、SQLite 或报告。未配置或连接失败时
返回 `not_configured`/失败状态，不伪造结果。

R3.1 起真实被动 DNS 入口同样显式受控：

```powershell
uv run surface-intel dns-health --real
uv run surface-intel dns-lookup --task-root .\task <task-id> --domain www.example.com --real --confirm-real
```

`dns-lookup` 要求任务范围已确认且域名在授权范围内；默认查询 A/AAAA/CNAME/NS/
MX/TXT/SOA（`--types` 可收窄，白名单外类型被拒绝）。每个记录类型是独立动作，
经统一执行器写入 `adapter_call` 审计并受任务 `dns_resolver` 预算约束。解析出的
CNAME 目标、IP 和第三方域名只产生 `candidate` 观察，不会被递归解析或确认为资产。

R3.2 起真实被动 CT 入口同样显式受控：

```powershell
uv run surface-intel ct-health --real
uv run surface-intel ct-search --task-root .\task <task-id> --domain example.com --real --confirm-real
```

`ct-search` 查询证书透明度日志，保存 CN、SAN、issuer、serial、validity、
log source 和 first_seen/last_seen。证书 SAN 只产生 `candidate` 关系，任何
第三方或范围外域名都会把观察升级为 `needs_review`；CT 匹配单独绝不确认资产
归属。默认源 crt.sh；其长时间不可用时可设 `SURFACE_INTEL_CT_SOURCE=ctlogs`
切换 ctlogs.dev 备用源（`SURFACE_INTEL_CT_API_KEY` 可选提高限额）。

R3.3 起真实被动注册数据入口同样显式受控：

```powershell
uv run surface-intel rdap-health --real
uv run surface-intel rdap-lookup --task-root .\task <task-id> --target 203.0.113.5 --kind ip --real --confirm-real
```

`rdap-lookup` 经 rdap.org（`SURFACE_INTEL_RDAP_ENDPOINT` 可覆盖）查询授权域名或
IP 的注册组织、网络、CIDR、abuse 联系和事件时间；TLD 无 RDAP 服务时回退
WHOIS（端口 43，只提取白名单字段），回退结果明确标记
`whois_fallback`/`whois_text_unverified`。注册数据只能产生 `candidate` 归属
关系——组织名或 ASN 相同绝不确认资产归属；ASN 直查在 R4 归并层提供绑定依据前
被拒绝。

P0 只提供离线项目骨架、
SQLite 状态、配置、状态枚举、脱敏日志以及 `init`、`create`、`status`、`validate`
命令；不会访问真实目标、FOFA 或 Playwright。

## 安装与验证

```powershell
uv sync --extra dev
uv run pytest
uv run surface-intel init .\task
```

未确认授权范围的任务会进入 `awaiting_scope`，`validate` 返回非零状态并禁止外部调用。
FOFA 仅会通过后续的独立适配器引用 `E:\Work\Copilot\fofa-mcp`，不会复制或修改该项目。
