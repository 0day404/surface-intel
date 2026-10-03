# R3 checkpoint: ctlogs.dev alternative CT source

## 阶段

R3.2 补充增量：CT 备用数据源（crt.sh 长期故障的运营性修复）。

## 背景与目标

crt.sh 持续 502（第三方监测：30 天 uptime 约 44.6%，2026-10-03 当天仍有约
2 小时中断）。为解除单一 CT 源依赖，按既有 `CtLogClient` 注入协议增加
ctlogs.dev 备用源，crt.sh 保留为默认；不改变任何门控与分类语义。

## 已完成

- `CrtlogsDevClient`（`ct_client.py`）：
  - 搜索 `https://ctlogs.dev/search?q=<domain>&output=json`（URL 参数
    percent-encoding、可注入 fetch、429→rate_limited、其他 HTTP→http_error、
    超时/坏响应结构化映射，与 CrtShClient 同契约）；
  - 搜索行只含匹配名（`match`）与 `san_count`，无 SAN 列表：`sans` 初始为
    [匹配名]，`san_completeness=match_only`，`common_name`/`first_seen`/
    `last_seen` 为 None；
  - best-effort detail 回填（默认前 5 条，`detail_limit=0` 关闭）：
    `GET https://api.ctlogs.dev/v1/cert/{id}` → 完整 `san_dns`（小写归一）、
    `subject_cn`→CN、`issuer_dn`（完整 DN 覆盖搜索行的 issuer CN）、
    `sightings[].sct_ts` min/max → first_seen/last_seen，成功后
    `san_completeness=full`；任何 detail 失败静默保留 match-only 行，不使
    搜索失败；
  - `SURFACE_INTEL_CT_API_KEY` → Bearer header（匿名限额 100 请求/小时，
    每次搜索消耗 1 + min(detail_limit, 行数) 个请求）。
- 源切换：`SURFACE_INTEL_CT_SOURCE`（`crtsh` 默认 / `ctlogs`），未知取值
  raise `unknown_ct_source:*`，非法 detail limit raise
  `invalid_ct_detail_limit`——错配源应响亮失败而非静默换源。
- 证书行 schema 补充：`san_count`、`san_completeness`
  （`match_only`/`full`，crt.sh 行为 `None`）经 `_certificate_row` 透传入
  observation；R4 归并层可据此甄别 SAN 完整性。
- 修复：统一 User-Agent `surface-intel/0.1.0 (passive CT lookup)`——通用
  python-urllib UA 被 ctlogs.dev 403（实测确认），crt.sh 亦受益。

## 安全语义（不变）

- 分类仍作用于可见名称：匹配名在范围内 → `candidate`；contains 类匹配噪声
  （如 `notexample.org` 命中 `example.com` 查询）照常升级 `needs_review`；
  CT 观察绝不产生 `confirmed`、绝不创建 asset。
- 已知覆盖缺口：match-only 行看不到未匹配的第三方 SAN，可能少报
  `needs_review`（只欠报、不误报）；detail 回填可缓解。`san_completeness`
  字段使欠报可检测。

## 新增/修改文件

新增：`tests/contract/test_ctlogs_client.py`（21 项）。
修改：`src/surface_intel/ct_client.py`（新 client + 归一化 + 源切换 + UA）、
`src/surface_intel/adapters/ct.py`（证书行透传字段）、
`tests/contract/test_ct_adapter.py`（透传断言）、README、ADAPTERS、OPERATIONS、
SCHEMA、REAL_COLLECTION_TASK_PLAN。

## 测试命令和结果

```powershell
cd E:\Work\Copilot\surface-intel
uv sync --extra dev
uv run pytest -q
```

结果：`171 passed`（151 + 新增 20：ctlogs client 契约 19、adapter 透传 1）。
覆盖：match-only 映射、detail 回填（SAN/CN/DN/时间戳、限额、失败降级、
limit=0 跳过）、空结果、无 rows 信封/坏响应/429/503/超时、URL 编码、
health 配置、Bearer header、归一化健壮性、环境变量源切换与错配报错、
runner 端到端（candidate 与 needs_review 升级路径）。

## 外部调用状态

fixture 测试全部使用注入 fetch，零网络。实现完成后仅以 `example.com`
（非授权目标、公开测试域）做过一次真实连通性验证：`observed`，17 行，首条
detail 完整回填（sans 含 `*.example.com`，first/last_seen 来自 SCT 时间戳），
耗时 ~1.8s。未接触任何用户授权目标；`E:\Work\Copilot\fofa-mcp` 未触碰。

## 已知限制

- ctlogs.dev 免费档数据窗口：当前/90 天内过期证书；2013 年起全档案需 Pro。
- 搜索分页未实现（与 crt.sh 相同的限制，匿名限 10 页）；大域名首页 1000 行
  截断。
- match-only 欠报第三方 SAN（见上），detail 回填默认仅前 5 条。
- 无自动故障转移：源选择是显式环境变量，不做 crt.sh→ctlogs 静默 fallback
  （跨源静默切换会让 evidence 的 `log_source` 不可预测；如需自动转移属
  R5 可靠性工作）。

## 下一步

R3 维持完成状态。R4 资产归并时：以 `san_completeness` 甄别 CT 行 SAN 完整
度；如运营需要，可在 R5 增加 crt.sh→ctlogs 带标记的自动转移与限速。

## 是否可恢复

是。备用源不改变持久化模型：观察/证据仍落盘 `evidence`/`observation`
（source=`ct_log`），`log_source`/`san_completeness` 在 payload 内区分来源
与完整度；重跑是合法重新观察。
