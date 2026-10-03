# R4 checkpoint: ASN lookups unlocked by merge bindings

## 阶段

R4.3 ASN 绑定直查（R4 第三增量，兑现 R3.3 的"R4 提供归并依据后放开"承诺）。

## 目标

ASN 查询在两种可审计依据下放行：(a) 范围文档 `authorized_asns` 显式授权；
(b) R4 归并绑定——`rdap_asn` 关系且来源资产 scope confirmed。无依据拒绝；
全路径审计透明；ASN 数据语义不变（candidate 归属）。

## 已完成

- **`Scope.authorized_asns`**（`policy/scope.py`）：补齐计划 §4.2 冻结的
  scope 模型字段（from_dict/to_dict/is_in_scope）。ASN 规范化仅数字
  （`AS64512`/`64512` 等价）；非数字项不参与匹配（None 守卫防误匹配）。
- **归并绑定门**（`rdap_runner._asn_merge_binding`）：查询 `asset_relation`
  中 relation_type=rdap_asn、from 资产 scope_status=confirmed 的绑定。
- **动作构造**：绑定路径下动作 `target` 保持 in-scope 锚点（通过 R1 执行器
  的 scope 检查，零执行器改动），ASN 经 `lookup_target` 携带，
  `scope_basis=merge_binding:<relation_id>`；三者全部进入 `adapter_call`
  input_summary 审计。RDAP 适配器优先以 `lookup_target` 发起查询，观察
  `target`=被查 ASN、新增 `scope_anchor` 字段记录锚点。
- **错误语义更新**：`asn_scope_binding_deferred`（R3 临时拒绝）→
  `asn_binding_not_found`（无授权亦无绑定的最终拒绝）。
- **CLI**：`rdap-lookup` 对 kind=asn 跳过 target 预检（授权判定在 runner）。

## 新增/修改文件

新增：`tests/integration/test_rdap_asn_binding.py`（3 项：端到端解锁、未
confirmed 来源不算依据、审计含 anchor+lookup_target+scope_basis）。
修改：`src/surface_intel/policy/scope.py`（authorized_asns）、
`src/surface_intel/rdap_runner.py`（绑定门+动作构造）、
`src/surface_intel/adapters/rdap.py`（lookup_target/scope_anchor）、
`src/surface_intel/cli.py`（ASN 预检例外）、
`tests/security/test_rdap_scope.py`（错误更名 + authorized_asns 路径）、
README、OPERATIONS、REAL_COLLECTION_TASK_PLAN。

## 测试命令和结果

```powershell
cd E:\Work\Copilot\surface-intel
uv sync --extra dev
uv run pytest -q
```

结果：`251 passed`（247 + 4）。覆盖：未绑定拒绝、authorized_asns 直通、
IP→merge→ASN 端到端解锁（观察 target=ASN、scope_anchor=锚点、归属仍
candidate）、来源未 confirmed 的关系不构成依据、审计行完整记录
anchor/lookup_target/scope_basis。

## 外部调用状态

全部 fixture；`fofa-mcp` 未触碰。

## 已知限制

- 绑定只在同任务内成立（跨任务共享归并依据未实现，也暂无需求）。
- `authorized_asns` 目前仅 CLI/库入口可用——`create` 命令的 scope 输入解析
  是否接受该字段取决于 normalization 层，未专项验证（范围文档由用户确认
  流程产生，R4.3 只保证运行时语义）。
- ASN 观察的 `related_scope_status` 恒为 candidate；ASN 本身确认资产仍属
  R4+ 范畴（需来源质量评估）。

## 下一步

R4 剩余项：报告工作表接入归并资产/关系/冲突（原计划 R7 统一报告的输入
准备）、FOFA 观察接入归并（端口/协议/service 资产）。两者均可独立增量；
R4 主体（归并、分层、冲突、架构识别、ASN 放开）已完成。

## 是否可恢复

是。绑定判定为纯 SQL 查询派生态；动作审计在 `adapter_call`；ASN 观察
落盘 evidence/observation，语义与既有增量一致。
