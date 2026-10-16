# FIX-2026-10-04 checkpoint: 18 项实测缺陷修复（scope-amend 扩权 / recon RDAP / 合并层 / 报告）

## 阶段

实测问题全量修复（issues 来源：`E:\Work\Claude\sec\docs\surface-intel-issues.md`，
ClientCorp 三任务实测 18 条，A/B/C/D 四类）。

## 目标

消除实测踩中的阻断与绕行成本：scope-amend 不能开主动观察（A1/A2）、recon
RDAP blocked 不醒目（A3）、R4 对第三方托管 IP 一刀切（A4）、主动观察不落
资产（B1）、报告单目录覆盖（C1）、信息质量与语义澄清（B2-B6/C2-C4/D1/D2）。
fail-closed 语义全部保持：intrusive_test 经 argparse choices 物理不可达；
所有扩权均为显式 opt-in 并入审计。

## 已完成

- **迁移 12**（`state/database.py`，SCHEMA_VERSION 11→12）：
  `asset.attributes_json`（latest-wins 观察属性）+ `redaction_map` 表
  （task_id, placeholder, plaintext，本地映射）。原地 ALTER，v11 数据无损
  （专项迁移测试）。
- **B5 conflict 抑制**（`merger.py`）：同一 observation 内多值（RDAP
  registrant/admin/tech 多组织名）不再互报 unresolved conflict；仅当不同
  observation 的值**集合**不一致才报。values_json 仍保留全部 distinct 值。
- **B1 主动源入合并层**（`merger.py` + `analyst_report.py`）：
  `_EXTRACTORS` 补 `safe_http`/`safe_port`。safe_http 刷新 host 资产属性
  （http_status/http_server/http_final_url/http_content_type，存
  attributes_json）；safe_port 对 IP 目标生成 service 资产
  （`ip:port/protocol`，protocol 证据化：仅 TLS 证据给 https）+
  `service_ip` relation，域名目标记属性 + `service_requires_ip_host` skip。
  失败传输（tls_error/timeout/redirect_blocked）不产生资产。anchor 守卫
  不变（伪造观察仍只能是 candidate）。analyst 站点表扩 safe_http 行、
  服务表经 service 资产自动填充。行构造器抽为模块级函数供合并报告复用。
- **A1/A2 scope-amend 扩权**（新 `policy/scope_amend.py` + `cli.py`）：
  `--allow-active`（+`--active-level safe_discovery|safe_probe` 默认前者、
  `--ports`）、`--confirm-new-domain`（用户显式确认未发现域名，审计记
  `user_confirmed_new=true`）。snapshot+task 指针+`scope_amended` event
  同一事务（cli 与 recon 共用单一落库路径）。`scope_warnings()`（scope.py）
  在 create 时对 `active_testing_allowed=true` 而 level 缺失/非法/矛盾
  输出 warnings（不改变 fail-closed 行为）。
- **A3 recon RDAP 醒目化 + 自动补录**（`full_recon.py`）：
  `FullReconResult.rdap_blocked`（blocked IP 清单顶层可见）；
  `--auto-amend-discovered-ips`（DNS A 记录揭示的 IP 在 RDAP 阶段前自动
  amend，event source=`auto_recon_dns_a` 含 revealed_by 映射；FOFA 来源
  IP 不纳入——第三方索引数据不自行扩权）。
- **A4 第三方托管降级放行**（`safe_http_runner.py`/`safe_port_runner.py`/
  cli）：`--allow-hosted-third-party` 仅解除 `third_party` 拦截
  （cdn/waf/lb/cloud_hosting 仍拦），override 本身记 medium review 项
  （architecture_override:*），blocked review 项 reason 附补救路径。
  runner 结果新增 `override_used`。
- **C1 报告防覆盖 + 多任务合并**（`export.py` + 新 `reporting/merged.py`）：
  输出改 `reports/<task-id>/`（`report_output_dir` 单一事实源，旧扁平目录
  不迁移）；`report --merge <task-id>...` 输出
  `reports/merged/<code8>/`（code8=排序 task-id 集合的 sha256 前 8 位，
  确定性命名），含 manifest.json + merged_report.xlsx（报告摘要含合计行
  + 域名/IP/站点/服务四主表，全部带 task_id 首列；同值资产跨任务各占一行，
  不跨任务归并 scope 判定）。
- **C2/C4**：validate 的 `external_calls_allowed` 改真实布尔并补
  active_testing_allowed/level 真实字段（旧 "not_implemented" 是错误哨兵，
  门控一直存在）；report summary 补 `budget_configured`（区分未配置与
  零消耗；预算为 opt-in，BudgetLedger 当前无生产接线）。
- **C3 email 脱敏闭环**（`redaction.py` + 四个存储点 + `export.py`）：
  email → `{{EMAIL_<sha256(task_id:email)[:8]>}}` 确定性占位符，明文存
  本地 `redaction_map`（与 evidence 同事务，evidence sha256 覆盖脱敏后
  payload）；`report --reveal-emails`（含 --merge 并集还原）显式还原；
  上游 `{{EMAIL_*}}` 占位符无映射条目自然透传。交付物默认只含占位符。
- **D1 CT 健康诚实化**（`ct_client.py`/`ct_runner.py`/cli）：health 明示
  `verified:false` + `configuration_check_only_no_network`（不发请求契约
  不变）；failover marker 的 `primary_error` 修为真实细节
  （error→error_type→`http_code:<n>`，原来恒 None）；`--probe`（必须
  `--real --confirm-real` 同给）真实轻量探针；ct-search/recon 汇总
  `failovers` 计数。
- **B2/B3/B4/B6**：DNS partial 的 `DnsRunResult.failed` 结构化列出失败的
  (record_type, resolver, error)；safe_http tls_error 记录
  error_type/error 明细、safe_port tls 分支补 error_type、适配器 observation
  透传 error 字段；redirect_blocked 记录 `blocked_url` 且 redirect_chain
  保留原始 Location（仅记录不请求）；`fofa-search --domain` 支持逗号分隔
  多域名（单 OR 查询，逐域 scope 校验，单域名字节级兼容旧查询）。
- **D2-3**：`main()` 抑制 Windows proactor 的 `unclosed transport`
  ResourceWarning（3.14 已知无害析构噪音）。

## 测试

- `uv run pytest`：**397 passed**（新增 ~45 个用例：迁移 12 原地升级、
  conflict 抑制矩阵、safe_http/safe_port 合并与 analyst 行、scope-amend
  扩权全矩阵 + intrusive_test 不可达 + confirm-new-domain 审计、create
  warnings、recon rdap_blocked/auto-amend 三态、report 子目录防覆盖、
  merged 报告内容/确定性/互斥、A4 override 矩阵、ct-health 零请求契约与
  probe 门控、failover 计数、C2 布尔、C3 占位符/还原/透传、C4 两态、
  B2/B3/B4/B6 各专项）。
- test_runtime_schema 断言 11→12 同步；test_ct_failover 的 primary_error
  期望按 D1 语义更新（None→http_code:502）。

## 已知边界

- merge 摘要中 safe_http/safe_port 观察从 skipped 转 processed（计数含义
  变化，测试未锁旧值）。
- 旧扁平 `reports/` 下历史产物不迁移；`build_report` 直调（测试/外部脚本）
  仍写传入目录。
- service 资产 taxonomy 维持 IP-keyed（normalize 约束），域名目标的
  port-check 结果记录在 host 资产属性。
- C3 明文仍存于本机 task.sqlite（redaction_map）；交付物默认脱敏，
  `--reveal-emails` 为单一显式开关。
- 真实外部调用的端到端冒烟（含 Windows asyncio 警告消失验证）待下一次
  授权 recon 顺带验证。
