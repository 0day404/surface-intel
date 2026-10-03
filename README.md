# surface-intel

`surface-intel` 是一个授权范围内的攻击面情报编排器。当前已提供离线 P0-P3
最小闭环：

- `init`、`create`、`preflight`、`plan`、`run`、`status`、`validate`、`report`；
- 范围标准化、排除项优先、动作幂等、Mock 被动/FOFA 多页适配器；
- SQLite 观察/证据/资产/缺口索引，以及可重建 JSON/XLSX。

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
