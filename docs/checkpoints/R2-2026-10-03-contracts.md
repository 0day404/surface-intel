# R2 checkpoint: FOFA 契约与批次持久化第一增量

## 已完成

- 检查独立 `E:\Work\Copilot\fofa-mcp`：
  - 入口为 `uv run fofa-mcp`；
  - transport 为 MCP stdio；
  - 工具包括 `fofa_health`、`fofa_account_info`、`fofa_search`、
    `fofa_search_structured`、`fofa_search_next`、`fofa_stats`、`fofa_host`。
- 未复制、移动或修改 `fofa-mcp`。
- `FofaMcpAdapter` 增加 transport-neutral `McpCallClient` 边界，支持注入函数
  或实现 `call_tool()` 的宿主客户端。
- FOFA manifest 声明七个工具能力。
- 健康调用失败会明确返回 `unknown/not_configured`，不会伪造成功。
- schema migration version 6。
- 新增 `fofa_query_batch` 和 `fofa_cursor`，保存 query fingerprint、批次、
  scope version、request id、next token、cursor page、成本、结果数量和失败状态。
- 新增 `FofaBatchRepository`，支持批次幂等创建、启动、完成、失败和 cursor 恢复。
- 增加多页、next token、健康失败、批次恢复和独立失败 contract/recovery 测试。

## 验证

```powershell
cd E:\Work\Copilot\surface-intel
uv run pytest -q
```

结果：`39 passed`。

## 未完成

- 尚未实现真实 MCP stdio client 的进程生命周期、initialize/tools/call 协议和
  连接恢复。
- 尚未把 FOFA batch repository 接入真实生产查询 runner。
- 尚未实现超长查询拆分、FOFA F 点专用预算、stats/host 结果的完整证据关联。
- 未使用真实凭据，未执行真实 FOFA 查询。
