# 复盘：2026-10-04 CeramTec 全域名收集执行问题与修复清单

任务：`task-b166f90f87d34bea`（51 观察 → 19 资产 / 31 关系 / 2 冲突）
性质：首次以全链路真实数据跑通五源收集，暴露的问题按 P0（代码缺陷）/
P1（工作流缺陷）/ P2（已知限制）分级。每项修复完成后在行尾勾选。

## P0 代码缺陷

### P0-1 CT 故障转移未在调用层强制生效
- 现象：recon driver 首轮直接实例化 `CrtShClient()` → crt.sh 502 失败；
  第二轮改用 `configured_ct_client()` 才经 ctlogs.dev 成功。
- 根因：FailoverCtClient 只在"客户端选择层"（configured_ct_client）生效，
  runner 与调用文档均未约束 runner 必须接收带故障转移的客户端。
- 修复意见：`run_ct_search` 在 runner 层检测裸 CrtShClient 并自动包装
  FailoverCtClient（client 显式为 FailoverCtClient 或指定单源时不动）；
  ADAPTERS.md 增加警示。
- [x] 已修复（2026-10-04，见 git log retrospective 提交）

### P0-2 重复观察导致报告证书噪声
- 现象：CT 重跑后同一证书（cn+issuer+not_after 相同）在报告里出现 4 行
  （2 证书 × 2 次运行），运维会误读为 4 张证书。
- 根因：重观察合法（设计原则：不覆盖原始证据），但报告层无去重视角。
- 修复意见：`domains_certificates` 报告节按 (common_name, issuer,
  not_after) 去重并附 `observation_count`；原始观察不动。
- [x] 已修复（2026-10-04，见 git log retrospective 提交）

### P0-3 FOFA stdio 管道泄漏（ResourceWarning 刷屏）
- 现象：driver 直调客户端未 close()，Windows Proactor 退出时每次刷
  8 行 unclosed transport 警告；R2 只修了 CLI 路径。
- 根因：`StdioJsonRpcTransport`/`FofaStdioClient` 无异步上下文管理器，
  driver 作者没有自然的清理入口。
- 修复意见：实现 `__aenter__/__aexit__`；文档定为
  `async with configured_fofa_client() as client` 唯一推荐用法。
- [x] 已修复（2026-10-04，见 git log retrospective 提交）

## P1 工作流缺陷

### P1-4 发现 IP 的范围扩编无受控工作流
- 现象：RDAP 因发现 IP 不在 authorized_ips 被拒（门控行为正确）；操作
  只能手改 scope.json——无审计、无确认步骤、旧 scope 版本悬空。
- 根因：计划要求"重新执行范围判断"但没有受控入口。
- 修复意见：新增 `scope-amend` CLI：从 candidate 资产生成扩编提案 →
  人工 `--confirm` 写入新 scope_version + 事件留痕 + 旧动作冻结（R5 语义）。
- [x] 已修复（2026-10-04，见 git log retrospective 提交）

### P1-5 全量收集编排无受测代码路径（建议单独增量）
- 现象：五源顺序、发现回填、payload 查询全部由临时 driver 现场编写，
  连犯 4 错（错误 import、payload 键当列、漏绑定参数、函数重复）。
- 修复意见：编排提升为包内受测模块 `orchestration/full_recon.py`
  （`run_full_passive_recon()`），driver 退化为薄参数入口；五源顺序、
  json_extract 查询、发现回填全部有测试覆盖。
- [ ] 未修复——独立增量排期（编排提升为受测模块工作量较大）

### P1-6 integrity=unknown 语义混淆
- 现象：fofa-search/direct-runner 不消耗 plan 动作，报告 integrity 恒为
  unknown（3 个 planned 挂着），运维无法区分"真未完成"与"没走 plan"。
- 修复意见：报告 summary 增加 `planned_actions_pending` 计数并在
  integrity=unknown 时附原因说明（改动最小、保持诚实）。
- [x] 已修复（2026-10-04，见 git log retrospective 提交）

## P2 已知限制（文档记录，不修代码）

| 问题 | 现状 | 处置 |
|---|---|---|
| crt.sh 长期不可用 | 故障转移已工作（本轮经 ctlogs.dev 成功） | P0-1 修后自然关闭 |
| 真实多页未触发 | 授权数据集不足一页（has_more=false） | 更大授权范围时自然覆盖 |
| 浏览器 not_configured | 本机无 node/npx | 需要浏览器观察时先装 node |
| 归属冲突 2 项 | CN 托管商两个登记名（Liu xiao bao / Ren yan jun） | 已保留 unresolved，R4 人工裁决 |
| Windows asyncio 管道警告（driver 场景） | P0-3 修后 CLI 与推荐用法均无警告 | 随 P0-3 关闭 |

## 修复验证基线

修复前：`326 passed`（commit 8cd7224）。
每项修复必须：新增回归测试 + 全套通过 + 本文档勾选 + commit 引用。
