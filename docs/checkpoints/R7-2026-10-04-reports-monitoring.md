# R7 checkpoint: reports, monitoring diff, final acceptance

## 阶段

R7 全量增量（JSON 主结果增强 + XLSX 十六表 + 持续监控 + 最终验收）。
**R0-R7 全部完成。**

## 目标

报告成为可复核、可重建、可判断覆盖率的交付物；baseline 差异监控；设计
文档 case 与 CLI 全链路验收。

## 已完成

- **JSON 主结果**（`reporting/export.py` 重写）：
  - 新节：`report_version`、`summary`（状态/范围版本/完整性/资产与观察
    计数（按类型与来源）/证据数/未解决冲突/review queue 数/预算消耗/
    stop_events/生成时间）、`scope_authorization`（含 payload）、
    `adapter_status`、`budgets`、`coverage`、`quality`（integrity+冲突+
    review queue+gaps）、`services`、`domains_certificates`、`web_stack`
    （架构状态+逐条证据）、`browser.pages+network`、`snapshot_diff`、
    `merge_summary`（R4.5 契约保留）、顶层 `integrity_status` 兼容保留。
  - 可追溯性：资产 sources_json→observation_id、关系 provenance_json、
    架构证据 observation_id、evidence source+sha256+redaction_status。
- **XLSX 十六表**：报告摘要/范围授权/资产总表/服务/域名证书/Web技术/
  FOFA查询/架构关系/浏览器页面/浏览器网络/归并冲突/信息缺口/review_queue/
  证据文件/执行日志/checkpoint_recovery/快照差异。
  - 防公式注入（_excel_safe）沿用；**原子发布**（tempfile+os.replace，
    失败清临时文件）；**case-08**：XLSX 失败保留 JSON 主结果，
    `xlsx_status=failed`+错误，重试恢复；export 记 json/xlsx 路径/sha256/
    大小/时间/状态/report_version/一致性标记。
- **持续监控**（`monitor.py` + CLI `baseline-save`/`baseline-diff`）：
  - baseline 持久化为 phase=baseline 的 checkpoint（复用恢复模型）；
  - `diff_against_baseline`：added/disappeared/changed（字段级基线对比）/
    reappeared（R4.6 生命周期）/unchanged + scope_version_match 显式标记，
    版本错配不静默；
  - `absorb_baseline` 质量门：integrity ∈ {complete,partial} 且任务版本
    匹配才覆盖；failed/unknown/版本错配显式拒绝；
  - save/diff 纯本地读算（监控默认被动），主动验证沿用独立门控命令。
- **最终验收**（`tests/integration/test_r7_acceptance.py`）：
  - case-01（无主动+空结果 bounded 收口）、case-07 幂等基础、case-08
    （导出失败保 JSON+重试恢复）、干净目录 CLI 全链
    init→create→preflight→plan→run→status→report→merge→baseline；
  - case-02/03→R2、case-04→R6 架构门控、case-05→R6 分类、case-06→P4
    recovery、case-09→redaction/Playwright、case-10→R5 freeze（映射记录，
    均已有独立 fixture）；
  - cmd_report 输出补 summary 节。

## 新增/修改文件

新增：`src/surface_intel/monitor.py`、
`tests/integration/test_r7_acceptance.py`（5 项）。
修改：`src/surface_intel/reporting/export.py`（重写）、
`src/surface_intel/cli.py`（baseline-save/baseline-diff + cmd_report 增强）、
REAL_COLLECTION_TASK_PLAN（R7 清单全勾）。

## 数据库/Schema 变化

无 migration（baseline 复用 checkpoint 表 phase=baseline）。

## 测试命令和结果

```powershell
cd E:\Work\Copilot\surface-intel
uv sync --extra dev
uv run pytest -q
```

结果：`322 passed`（317 + 5）。覆盖：diff added/changed/unchanged 与版本
错配标记、absorb 质量门/版本门/通过路径、case-01 bounded、case-08 导出
失败保 JSON+重试、CLI 干净目录全链（含 merge/baseline）、R4.5 契约在
新报告结构下保持。

## 外部调用状态

报告与监控为纯本地读算，零网络；`fofa-mcp` 未触碰。真实 smoke 命令就绪
且多门控，留待用户授权目标后执行。

## 已知限制

- XLSX 单元格内 JSON 以文本呈现（人读可用，结构化展开留后续工具）。
- baseline 为全量快照（增量存储未做；资产量级当前可接受）。
- 监控差异验证（对新增/变化资产的主动复核）需单独门控调用（by design）。

## 下一步

R0-R7 全部完成：设计文档的被动多源闭环、归并、架构识别、预算/停止/调度
强化与三类真实观察（HTTP/端口/浏览器）均已落地并通过五类测试。R2 的
`bounded` 状态按计划保持，直到真实授权目标的完整 smoke test 通过后由
用户评审升级。

## 是否可恢复

是。报告/监控为派生数据幂等重算；baseline 在 checkpoint 表内随既有恢复
模型管理。
