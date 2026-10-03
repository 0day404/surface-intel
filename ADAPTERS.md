# Adapters

MockPassiveAdapter 和 MockFofaAdapter 用于离线 fixture；FofaMcpAdapter 只接受注入的
MCP 调用函数，不包含 FOFA 实现。FOFA 必须通过独立适配器调用
`E:\Work\Copilot\fofa-mcp`；Playwright 使用外部命令
`npx -y @playwright/mcp@latest`，不复制源码、依赖、缓存或凭据。
