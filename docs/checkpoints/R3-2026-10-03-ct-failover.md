# R3 checkpoint: CT automatic failover (crt.sh -> ctlogs.dev)

## 阶段

R3.2 补充增量 2：CT 源自动故障转移（应用户要求从 R5 提前）。

## 背景与目标

crt.sh 持续不稳定，前一增量已加入 ctlogs.dev 备用源（显式环境变量切换）。
本增量实现自动转移，同时解决此前拒绝静默换源的理由：所有转移**显式标记、
可审计**。

## 已完成

- `FailoverCtClient`（`ct_client.py`）：实现 `CtLogClient` 协议的包装器，
  primary=crt.sh、fallback=ctlogs.dev。
  - **转移条件**：主源返回基础设施失败——`rate_limited`/`timeout`/
    `http_error`/`invalid_response`/transport 异常（防御性捕获，正常情况
    client 自行映射）。
  - **不转移**：定案结果 `observed`/`empty` 永远可信、不重查——降级源的
    "空结果"不被二次猜测，避免跨源结果不一致。
  - **标记**：转移后的 outcome 合并 fallback 结果并附加
    `failover: {from, reason, primary_error, fallback}`；两源皆败时返回
    fallback 的失败 outcome + 同一标记（保留主源根因 `primary_error`）。
  - `health()` 汇报 `strategy=failover` 与两源配置。
- `source_name` 类属性（`crt.sh`/`ctlogs.dev`/`auto(crt.sh->ctlogs.dev)`），
  标记来源稳定可读。
- `configured_ct_client`：默认 source `auto`（无环境变量时即启用转移）；
  `crtsh`/`ctlogs` 锁定单源（无转移）；未知源仍响亮报错。ctlogs 配置解析
  抽出 `_configured_ctlogs_client` 共用。
- `CertificateTransparencyAdapter`：observation 新增 `failover` 字段透传
  （定案观察与 `_empty_observation` 失败观察均保留）；存储层
  （`store_unified_observations`）无需改动，标记随 payload 自动落盘
  evidence/observation。

## 安全语义（不变）

- 分类、预算、门控全部不动：转移发生在 client 层，adapter/runner 无感知；
  预算仍按动作记账（单动作最多两源各一次搜索 + 各自 detail 回填）。
- SAN/匹配名分类规则一致，CT 观察仍绝不产生 `confirmed`、不创建 asset。

## 新增/修改文件

新增：`tests/contract/test_ct_failover.py`（13 项）。
修改：`src/surface_intel/ct_client.py`（FailoverCtClient、source_name、默认
auto）、`src/surface_intel/adapters/ct.py`（failover 透传）、
`tests/contract/test_ctlogs_client.py`（默认源断言更新）、README、ADAPTERS、
OPERATIONS、SCHEMA、REAL_COLLECTION_TASK_PLAN。

## 测试命令和结果

```powershell
cd E:\Work\Copilot\surface-intel
uv sync --extra dev
uv run pytest -q
```

结果：`184 passed`（171 + 13）。覆盖：主源成功不触碰备源、定案 empty 不重查、
四类基础设施失败转移、两源皆败保留主因、transport 死亡转移（双向）、health
策略汇报、默认/显式源配置、source_name 稳定性、adapter 成功/失败观察透传、
runner 存储端到端。

## 外部调用状态

fixture 测试零网络。实连验证一次：`SURFACE_INTEL_CT_ENDPOINT=http://127.0.0.1:9/`
（强制主源不可达）+ 默认 auto，`example.com` 查询自动转移到 ctlogs.dev——
`observed` 17 行，`failover: {from: crt.sh, reason: timeout, fallback:
ctlogs.dev}`，log_source=ctlogs.dev。未接触任何用户授权目标；`fofa-mcp`
未触碰。

## 已知限制

- 最坏情况单动作跨两源各一个超时窗口（默认 2×30s）；统一速率/超时预算仍属
  R5。
- 仅支持两级（crt.sh→ctlogs.dev）；更多源/多级转移（如 Cert Spotter）待
  需求出现。
- 主源 `empty` 与备源可能的数据差异不做调和（按设计：定案可信）。

## 下一步

R3 维持完成状态（含运营性补充）。R4 资产归并：`failover` 标记与
`san_completeness` 一样作为归并时的来源质量输入。

## 是否可恢复

是。转移逻辑无状态（每次 search 独立判断）；标记随观察落盘，重启/恢复后
仍可从 evidence 重建完整来源链。
