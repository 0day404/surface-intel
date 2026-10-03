# Adapters

MockPassiveAdapter 和 MockFofaAdapter 用于离线 fixture；FofaMcpAdapter 只接受注入的
MCP 调用函数，不包含 FOFA 实现。FOFA 必须通过独立适配器调用
`E:\Work\Copilot\fofa-mcp`；Playwright 使用外部命令
`npx -y @playwright/mcp@latest`，不复制源码、依赖、缓存或凭据。

P4 增加 `CapabilityRegistry`，每个适配器必须声明能力、所需主动等级、成本、分页/
恢复/取消能力、外部副作用、重试和敏感数据策略。`SafeHttpAdapter` 仅允许授权上下文
中的 GET 观察；`SafePortAdapter` 仅允许授权端口验证，拒绝端口范围扫描。

P6 增加 `PlaywrightAdapter`。它只封装外部 MCP 调用，启动命令为
`npx -y @playwright/mcp@latest`；未配置时返回 `unknown/not_configured`。只允许授权
域名的公开页面观察，自动阻止第三方域名、登录、状态变更和下载。
