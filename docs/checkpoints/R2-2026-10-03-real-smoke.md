# R2 checkpoint: CeramTec FOFA real smoke test

## 授权与边界

- 目标：`www.ceramtec-group.cn`
- 授权依据：用户明确确认该域名属于已授权渗透测试项目
- 主动等级：`passive_only`
- 查询：`domain="www.ceramtec-group.cn"`
- 页面大小：10
- 未执行目标直连、端口扫描、浏览器观察或业务操作

## 任务

- task id：`task-9542e3d99077458c`
- scope version：`ceramtec-fofa-v1`
- batch id：`fofa-batch-7e50b596bb344fef`
- 结果：`completed`
- 页数：1
- 观察：2

FOFA 返回了两个被动观察，分别对应 HTTP/80 和 HTTPS/443；结果已脱敏后写入
SQLite 的 `evidence` 与 `observation` 表。未将 stats 或摘要直接转为资产。

## 报告验证

```powershell
uv run surface-intel report --task-root .\runtime\ceramtec-fofa-20261003 task-9542e3d99077458c
```

已生成 `result.json` 和 `result.xlsx`，报告完整性状态为 `complete`。

## 修复

真实 MCP 成功响应使用 `ok/data/pagination/cost`，旧适配器只识别
`status/observations`，导致首次查询被误判为失败。现已修复，并加入真实格式
契约测试；同时传递 `confirm=true`、`task_id`，保存 consumed/required F points。
完整测试结果：`47 passed`。

Windows asyncio 管道关闭警告也已修复；`fofa-health --real` 验证无该清理警告。

## 限制

- 本次只验证一个搜索页；没有证明全部分页、长查询拆分和多批次真实数据覆盖。
- 真实账号成本字段在本次返回中为空/未映射为可计费 F 点，不能据此推断额度。
- 真实结果仍需后续资产归并、架构识别和多源交叉验证。
