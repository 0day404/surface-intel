# R7 checkpoint: ClientCorp FOFA full-chain real smoke (R7 acceptance)

## 授权与边界

- 目标：`www.example.com`（示例公司，用户确认
  的已授权渗透测试项目目标，与 R2 smoke 同一授权）
- 主动等级：`passive_only`（active_testing_allowed=false）
- 外部动作：仅 FOFA 被动查询 `domain="www.example.com"`，size=10，
  消耗 0 F 点（batch `fofa-batch-0cee42a3b60745a4`）
- 未执行目标直连、端口扫描、浏览器观察或业务操作

## 执行链路（R7 全链首次真实验证）

干净任务根 `runtime/client-r7-smoke-20261004/`：
`init → create → preflight（3 目标规范化）→ plan（3 被动动作）→
fofa-search --real → merge → baseline-save → baseline-diff → report`

- task：`task-745c9d216873491a`，scope `client-r7-smoke-v1`
- FOFA 结果：2 条观察（HTTPS/443 与 HTTP/80，ip 192.0.2.1，标题
  "ClientCorp"），证据脱敏落盘，consumed_fpoints=0
- 归并：5 资产（2 domain confirmed + 1 ip candidate + 2 service candidate）、
  4 关系（fofa_host_ip/fofa_domain_ip/service_ip）、0 冲突；IP 与服务
  正确保持 candidate（IP 未列入授权清单，开放/解析≠授权）
- 架构识别：1 项 third_party 标记（FOFA 揭示的范围外主机）
- 报告：JSON+XLSX 导出成功（sha256 记录、asset_count_consistent=true）；
  integrity=unknown 是诚实状态——`plan` 的 3 个被动动作仍 pending
  （未用 mock 数据污染真实任务）

## Smoke 发现并修复的真实缺陷

1. **naive/aware 时间崩溃**：FOFA 观察无 observed_at，回退 SQLite
   CURRENT_TIMESTAMP（naive）与 aware 的 now 相减 → merge TypeError。
   修复：`_parse_timestamp` 统一把 naive 视为 UTC；merge CLI 增加结构化
   异常输出（不再裸 traceback）。
2. **兄弟观察生命周期误判**：FOFA 每行结果独立 observation，"最近一条"
   判定把兄弟服务行误标 disappeared。修复：同锚点+类型、时间戳落在最新
   观察 120 秒窗口内的观察视为同轮兄弟，全部 current（DNS 式跨小时重跑
   仍构成独立轮次）；补回归测试。
3. **last_transition 被幂等重跑抹除**：无状态变化的重复归一会把历史
   transition 覆写为 NULL。修复：仅在有状态转换时写入 last_transition；
   补回归测试。

修复后全套测试 `324 passed`（+2 回归）。

## fofa-mcp 完整性

smoke 前后 `git status` 干净，未修改 `E:\Work\Copilot\fofa-mcp`。

## 结论

R7 验收中的"真实外部服务最小 smoke"完成：全链路（含归并/监控/报告）在
真实 FOFA 数据上工作正常，发现的 3 个缺陷已修复并有回归测试覆盖。R2
`bounded` 状态维持——完整升级评审（多页/拆批/多源交叉）留待用户决定。

## 下一步

路线图 R0-R7 全部完成。可选后续：真实 HTTP/端口/browser 观察 smoke
（需范围开启 active_testing 并单独确认）；FOFA 多页与拆批真实覆盖。
