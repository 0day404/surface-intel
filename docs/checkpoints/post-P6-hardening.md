# Post-P6 hardening checkpoint

阶段：P6 后恢复与交付硬化  
目标：确保暂停/取消、FOFA 分页恢复、动作终态和报告恢复摘要可验证。

已完成：

- `run` 读取最新 FOFA checkpoint，避免已完成页从第一页重放。
- 已完成动作写入 `completed_at`，重复运行跳过已完成动作。
- 执行器在任务暂停或取消后停止后续分页。
- `run` 支持 `serial`、`delegated`、`hybrid` 模式参数（当前离线执行器共享同一安全路径）。
- 报告增加证据、事件、checkpoint、恢复摘要工作表和 JSON 字段。
- `resume` CLI 明确接受 `--reason`，避免控制命令参数错误。
- 增加恢复幂等集成测试。

验证：

```powershell
uv run pytest -q
```

结果：`21 passed`。未启动真实外部服务，未访问真实目标。
