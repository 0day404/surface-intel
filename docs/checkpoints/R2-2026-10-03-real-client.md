# R2 checkpoint: 真实 FOFA stdio 客户端与受控查询入口

## 已完成

- 新增 `StdioJsonRpcTransport`：
  - 启动外部 `uv run fofa-mcp`；
  - MCP `initialize`；
  - `notifications/initialized`；
  - `tools/call`；
  - request ID 匹配；
  - 超时；
  - 进程关闭和异常分类。
- 新增 `FofaStdioClient` 和 MCP 结构化结果解包。
- 新增 `run_fofa_query`：
  - query fingerprint；
  - batch 创建/恢复；
  - `fofa_search`/`fofa_search_next`；
  - next token 持久化；
  - 成本和结果数量；
  - 缺 token、失败批次和最大页数处理。
- CLI 增加显式受控入口：
  - `fofa-health --real`
  - `fofa-search --task-root <dir> <task-id> --domain <domain> --real --confirm-real`
- 真实搜索要求任务范围已确认且域名在授权范围内；不允许通过命令参数传递
  FOFA 凭据。

## 验证

```powershell
cd E:\Work\Copilot\surface-intel
uv run pytest -q
```

结果：`43 passed`。

## 真实调用状态

本 checkpoint 只完成真实客户端和受控入口实现，未使用真实凭据，未访问真实
FOFA 查询目标。实际 smoke test 仍需明确的授权任务、授权域名和受控 FOFA
凭据配置；凭据应只存在 FOFA MCP 的环境或受控凭据存储中。

## 未完成

- 真实 MCP 进程在当前环境的端到端 smoke test。
- 超长查询拆分。
- stats/host/account 结果的完整证据关联。
- 真实调用的 FOFA F 点和多级预算映射。
