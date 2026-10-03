# R6 checkpoint: real observation (Safe HTTP / Safe Port / Playwright)

## 阶段

R6 全量增量（R6.1 Safe HTTP + R6.2 Safe Port + R6.3 Playwright）。

## 目标

接入三类真实观察：低影响 HTTP（GET/HEAD、逐跳授权、摘要化）、显式端口
验证（无扫描原语）、外部 Playwright MCP 公开页观察——全部受 R4 架构门控、
R5 预算/停止门控与主动授权门控约束。

## 已完成

- **R6.1 Safe HTTP**（`safe_http_client.py` + `safe_http_runner.py` +
  适配器扩展）：见 checkpoint `R6-2026-10-04-safe-http.md`。
  命令：`http-observe --real --confirm-real [--method GET|HEAD]`。
- **R6.2 Safe Port**（`safe_port_client.py` + `safe_port_runner.py` +
  适配器扩展）：
  - 真实 socket 连接（to_thread），五态建模 open/closed/timeout/filtered/
    tls_error；TLS-likely 端口自动 wrap，握手成功记对端证书 SHA-256，握手
    失败仍 open+tls_error（端口确实应答）。
  - 协议猜测为端口表提示，置信度有界 0.5——猜测不是识别。
  - **scope `allowed_ports` 字段落地**（计划 §4.2 冻结模型）：空清单授权为
    零；未列/相邻端口 `port_not_authorized` 且零流量；无扫描原语。
  - 开放观察恒 `candidate`（开放≠归属、不解锁门控），closed 为
    not_applicable 定案。架构门控同 HTTP（共享阻断集与 review item）。
  - 命令：`port-check --real --confirm-real --target --port`。
- **R6.3 Playwright**（`playwright_client.py` + `playwright_runner.py` +
  适配器扩展）：
  - stdio JSON-RPC 客户端复用 FOFA 传输（`StdioJsonRpcTransport`），启动
    `npx -y @playwright/mcp@latest --stdio`；initialize 握手一次，健康检查
    显式 not_configured 原因（node 缺失/启动失败/MCP 错误）。
  - 公开页观察：browser_navigate/console/network/screenshot 工具链；网络
    摘要第一层丢弃 headers/cookies/post_data 键（redact 第二层兜底）；
    scope_domains 浏览器范围对象——范围外 origin 记入 third_party_origins
    （→needs_review），不继续访问；max_requests 资源上限。
  - 适配器契约拒绝 allow_login/allow_state_change/allow_download；观察
    target=URL、scope_anchor=host，审计一致。
  - 命令：`browser-observe --real --confirm-real --url [--max-requests]`。

## 新增/修改文件

新增：`src/surface_intel/safe_http_client.py`、`src/surface_intel/safe_http_runner.py`、
`src/surface_intel/safe_port_client.py`、`src/surface_intel/safe_port_runner.py`、
`src/surface_intel/playwright_client.py`、`src/surface_intel/playwright_runner.py`、
`tests/contract/test_safe_http_client.py`、`tests/integration/test_safe_http_runner.py`、
`tests/integration/test_safe_port.py`、`tests/contract/test_playwright_client.py`、
`tests/integration/test_playwright_runner.py`。
修改：`src/surface_intel/adapters/safe_http.py`、`adapters/safe_port.py`、
`adapters/playwright.py`（锚点/富映射/状态归一）、`policy/scope.py`
（allowed_ports）、`cli.py`（三个命令）。

## 数据库/Schema 变化

无 migration（scope payload 增加 allowed_ports 字段；复用既有表）。

## 测试命令和结果

```powershell
cd E:\Work\Copilot\surface-intel
uv sync --extra dev
uv run pytest -q
```

结果：`317 passed`（284 + 33：HTTP 客户端 10 + runner 7、端口 8、Playwright
客户端 5 + runner 6/修订断言）。

## 外部调用状态

全部注入 transport（零真实网络）；`fofa-mcp` 未触碰。Playwright 真实链路
需要本机 node/npx 与浏览器（健康检查显式报告 not_configured 原因）。真实
smoke 留待用户明确授权目标（范围文档须开启 active_testing）。

## 已知限制

- HTTP：HTTP/1.1 语义；DNS 预解析 TOCTOU 窗口（连接级二次校验留 R7）。
- 端口：非 TLS 端口的协议识别仅提示；filtered 与 closed 的区分依赖错误
  映射（防火墙 REJECT 会呈现为 closed）。
- Playwright：SPA 路由/资源失败明细依赖 MCP 工具返回结构（解析容错，缺
  字段记 None）；截图由 MCP 写入其工作目录，仅引用元数据入 evidence。
- 三个 runner 均为单目标单动作；批量观察循环（带速率/预算）属编排层组合
  调用（R5 原语已就绪）。

## 下一步

R6 完成。进入 R7：报告、差异监控和最终验收（三源+FOFA+主动观察统一报告、
监控差异、S-12/S-13 验收、R2 `bounded`→最终验收评审）。

## 是否可恢复

是。三个 runner 均为单动作 + 统一审计/落盘；无长事务；预算/停止由 R5 原
语在执行器层兜底。
