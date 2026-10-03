# Schema

当前 schema version 为 `1.0`，SQLite migration version 为 `7`。

适配器能力状态使用 `available`、`degraded`、`not_configured`、`unsupported`
和 `failed`。能力状态是预检和报告字段；`not_configured`、`unsupported` 或
`failed` 不能被替换成 Mock 成功结果后继续声明真实能力。

实体：`task`、`scope_snapshot`、`action`、`event`、`checkpoint`、`adapter_registry`、
`evidence`、`observation`、`asset`、`gap`。migration 2 为动作恢复增加
`attempt`、`started_at`、`completed_at`、`error_code`。
Migration 3 增加 `subtask`、`agent_lease` 和 `subtask_result`，用于租约、心跳、
超时恢复和重复结果提交保护。
Migration 4 扩展 `action` 的执行上下文和外部状态字段，并增加 `adapter_call`
审计表，用于统一执行器记录脱敏输入、适配器版本、request id、错误分类和完成时间。
Migration 5 增加 `budget` 表，支持按任务和预算类型进行事务性预留、提交和释放，
用于并发执行时避免成本超卖。
Migration 6 增加 `fofa_query_batch` 和 `fofa_cursor`，持久化 FOFA 查询指纹、
拆分批次、分页 token、request id、成本、结果数量和独立失败状态。
R2 收尾扩展批次的 `estimated_fpoints` 与 `consumed_fpoints`，并将 FOFA 搜索、
stats、host 和 account 操作的脱敏结果写入统一 evidence/observation 索引。
Migration 7 增加 `fofa_query_plan`，保存父查询、目标依据、预期字段和批次覆盖
关系，使超长查询拆分、结果覆盖率和失败批次可以从 SQLite 重建。

R3.1 无 migration 变化：DNS 观察复用 `evidence`/`observation` 索引（source 为
`dns_resolver`），执行审计复用 `adapter_call`，预算复用 `budget` 表的
`dns_resolver` kind。观察 payload 额外携带 `record_type`、`records`、`ttl`、
`resolver`、`dnssec`、`resolved_at`、`resolution_ms`、`source_quality`、
`license_status`、`scope_version`、`observed_at` 和 `related_scope_status`
（`confirmed`/`candidate`/`not_applicable`）。

R3.2 同样无 migration 变化：CT 观察复用 `evidence`/`observation`（source 为
`ct_log`），执行审计复用 `adapter_call`，预算复用 `budget` 表的 `ct_log` kind。
观察 payload 额外携带 `target`、`certificates`（每行 `common_name`、`sans`、
`issuer`、`serial`、`not_before`、`not_after`、`first_seen`、`last_seen`、
`log_id`、`log_source`）、`matched_count`、`log_source`、`queried_at`、
`query_ms`、`source_quality`、`license_status`、`scope_version`、`observed_at` 和
`related_scope_status`（`candidate`/`needs_review`/`not_applicable`——CT 匹配
单独绝不产生 `confirmed`）。R3.2 补充后证书行另含 `san_count` 与
`san_completeness`（`match_only`/`full`，crt.sh 行为 `None`）：ctlogs.dev 源的
搜索行只揭示匹配名，未回填 detail 前 SAN 列表不完整，R4 归并层可据此甄别。

R3.3 同样无 migration 变化：RDAP 观察复用 `evidence`/`observation`（source 为
`rdap_lookup`），执行审计复用 `adapter_call`，预算复用 `budget` 表的
`rdap_lookup` kind。观察 payload 额外携带 `target`、`target_kind`（domain/ip/
asn）、`record`（`handle`、`name`、`country`、`organizations`、`registrar`、
`abuse_contacts`、`events`、`cidrs`、`ip_version`、`start_address`、
`end_address`、`asn_start`、`asn_end`、`statuses`、`nameservers`，WHOIS 回退时
另含 `data_quality=whois_fallback`）、`source_protocol`、`queried_at`、
`query_ms`、`source_quality`（`direct`/`whois_fallback`）、`license_status`
（`public_rdap`/`whois_text_unverified`）、`scope_version`、`observed_at` 和
`related_scope_status`（`candidate`/`not_applicable`——注册数据绝不产生
`confirmed`）。

R3.4 统一收口：三个被动源的观察写入合并到
`state/observations.store_unified_observations`，契约键为
`UNIFIED_METADATA_KEYS`（`scope_version`、`observed_at`、`source_quality`、
`license_status`、`related_scope_status`），所有 `dns_resolver`/`ct_log`/
`rdap_lookup` 观察一律经过脱敏（`redact`）后写 `evidence`（redaction_status=
`redacted`，sha256 摘要）与 `observation`；原始响应体从不进入普通日志（日志端
`RedactingFormatter` 兜底）。跨源元数据契约、冲突共存、定案否定、超时/限流和
许可不可用由 `tests/integration/test_r3_multisource.py` 统一约束。

后续批次增加
`plan`、`asset`、`evidence`、`observation`、`gap`、`budget`、`adapter_registry`、
`review_item`、`subtask` 和 `agent_lease`，并通过迁移演进。
