# Final acceptance hardening checkpoint

阶段：最终离线验收硬化  
目标：避免报告质量门误报完整，并验证重启/重复运行的持久化语义。

已完成：

- 增加统一完整性质量门：
  - `complete`
  - `partial`
  - `failed`
  - `unknown`
- 任务暂停、失败、未知动作、失败动作和待执行动作不会被报告标记为 `complete`。
- 已进入归并阶段的任务重复执行会返回 `already_completed`，不重复写入观察和资产。
- 报告保留证据、事件、checkpoint 和恢复摘要。
- 增加离线验收矩阵测试：
  - 空被动结果；
  - FOFA 多页；
  - 敏感数据边界；
  - `started` 崩溃状态；
  - 质量门状态判定。

验证：

```powershell
uv run pytest -q
```

结果：`25 passed`。未访问真实目标、真实 FOFA 或 Playwright。
