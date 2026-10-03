# R4 checkpoint: asset model extension and passive merge engine

## 阶段

R4.1 资产模型扩展与归并核心（R4 第一增量）。

## 目标

把 R3 三被动源的 observation 幂等归并为统一资产模型：强/中关联分级、来源
provenance、置信度、当前/历史分层、冲突保留——纯本地计算，零网络请求。

## 已完成

- **Migration 8**（`state/database.py`，SCHEMA_VERSION 7→8）：
  - `asset` 扩展 `first_seen`/`last_seen`/`sources_json`（provenance：每来源
    observation_id 列表）；旧库经 ALTER 分支原地升级（有迁移测试覆盖）。
  - 新表 `asset_relation`：from/to asset、relation_type、relation_strength
    （strong/medium/weak）、status（candidate/confirmed）、provenance_json、
    UNIQUE(from,to,type) 保证幂等。
  - 新表 `asset_conflict`：asset + attribute + values_json（冲突值全保留）+
    status=unresolved。
- **归并引擎**（`merger.py`）：
  - 资产类型冻结 domain/ip/cidr/asn/organization；值规范化幂等（域名小写去
    尾点、IP 压缩规范形、CIDR 接受注册商缩写 `203.0.113/24` 并补零、ASN 仅
    数字）；规范化失败计入 `skipped`（normalization_failed），不静默丢弃。
  - 提取器：DNS（A/AAAA→ip、CNAME/NS→domain、MX→exchange、SOA→mname，TXT
    不产关系）、CT（SAN 按"值路由"——IP SAN 归 ip 资产、其余 domain）、
    RDAP（仅 observed：organizations/registrar→organization 资产、cidrs→
    cidr、asn_start→asn）。锚点目标资产 scope_anchored。
  - 关联分级按计划 §12.2：强=dns_a/aaaa/cname/ns/mx/soa + cert_name（显式
    DNS/证书绑定）；中=rdap_org/rdap_registrar/rdap_cidr/rdap_asn；weak 保留
    给相似性（被动结构数据不产生，也永不确认）。
  - 置信度公式确定性：max(关联基线)（strong 0.7/medium 0.5/weak 0.25）+
    多源佐证（每额外来源 +0.1，封顶 +0.2）；候选封顶 0.95；仅用户 scope
    锚点=1.0；观测确认资产（授权 IP）封顶 0.9，与锚点信用分离。
  - 当前/历史分层：重复归并 first_seen 取最早、last_seen 取最晚；变化的
    DNS 答案→两个 IP 资产并存（历史转换，不算归属冲突）。
  - 冲突保留：organization/registrar/asn 属性多值（跨 observation 不同值）
    → `asset_conflict` unresolved 行重写（幂等），两值都保留为资产，绝不
    择值；同一值重复观察不产生冲突。
  - scope 语义不变：domain/ip 按 scope 文档 confirmed/candidate；
    organization/cidr/asn 永远 candidate；关系 status=confirmed 仅当
    strong 且 to_asset confirmed。
- **CLI**：`surface-intel merge <task-id> --task-root .\task`，输出归并摘要
  JSON（processed/skipped/created/updated/conflicts + skipped 明细）；未知
  来源（fofa）计入 skipped 待后续增量；不修改任务状态、零网络。

## 新增/修改文件

新增：`src/surface_intel/merger.py`、`tests/contract/test_merger.py`（17）、
`tests/integration/test_merge_runner.py`（9）、
`tests/security/test_merge_scope.py`（6）。
修改：`src/surface_intel/state/database.py`（migration 8）、
`src/surface_intel/cli.py`（merge 命令）、
`tests/contract/test_runtime_schema.py`（版本 8 + 迁移路径测试）、
`tests/unit/test_p0.py`（版本断言）、README、OPERATIONS、SCHEMA、
REAL_COLLECTION_TASK_PLAN。

## 数据库/Schema 变化

SCHEMA_VERSION 7→8（见上）。`health()` 校验通过；v7→v8 原地迁移有专项测试
（旧 asset 行保留、新列/新表生成）。

## 测试命令和结果

```powershell
cd E:\Work\Copilot\surface-intel
uv sync --extra dev
uv run pytest -q
```

结果：`220 passed`（184 + 36：合约 17、集成 9、安全 6、schema/迁移 2/新增 1
+ 版本断言更新 2）。覆盖：规范化幂等/缩写 CIDR/拒绝集、关联分级、置信度
边界、scope 分类（归属类永不确认）、三源端到端、负观察不产资产、冲突产生
与不产生、重归并幂等 + first/last 分层、未知源跳过、规范化失败上报、多源
佐证提升置信度、宽 CIDR 不扩范围、多源一致仍 candidate、无弱关系、仅强+
confirmed 关系确认、冲突不择值、历史分层不覆盖。

## 外部调用状态

纯本地计算，零网络请求；`fofa-mcp` 未触碰。

## 已知限制

- FOFA 观察尚未接入归并（计入 skipped）——端口/协议/service/product 资产与
  `port`/`protocol` 字段随 FOFA 归并增量补齐。
- 架构识别未实现（architecture_status 仍 unknown）——R4.2。
- 时间衰减（confidence 随观测年龄衰减）未实现；当前证据等级+佐证模型。
- 删除/临时变化/重现等完整历史状态分类未实现；当前以 first/last_seen +
  多值并存表达。
- 规范化规则版本标识未落盘（规则在代码内冻结，SCHEMA.md 记录）。

## 下一步

R4.2 架构识别：从合并资产+观察识别 CDN/WAF/DNS 托管/云托管/第三方依赖，
写 `architecture_status`（candidate/likely 分级，永不 confirmed），输出
计划 §R4.3 的状态集与证据引用。随后 R4.3 以归并关系为绑定依据放开 RDAP
ASN 直查。

## 是否可恢复

是。归并是纯派生数据的幂等重算：输入 observation 不变则输出不变；可随时
删除派生行重跑（当前实现为覆盖式更新，不删旧行）。无外部状态。
