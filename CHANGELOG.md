# Changelog

## 0.2.0 (2026-10-04)

### 上一批：18 项实测缺陷修复（master 72f83e3，checkpoint `docs/checkpoints/FIX-2026-10-04-issues-batch.md`）

- scope-amend 扩权（`--allow-active/--active-level/--ports/--confirm-new-domain`）、
  recon 顶层 `rdap_blocked` + `--auto-amend-discovered-ips`、merge 白名单补
  safe_http/safe_port、报告写 `reports/<task-id>/` 子目录 + `report --merge`、
  email 脱敏 + `--reveal-emails`、ct-health 诚实化 + `--probe`、
  `--allow-hosted-third-party` 观察覆盖留痕等。

### 本批：D 包（双项目调研产出）

- **D4 playwright health 诚实化**：判活改为 initialize + tools/list（探测
  `browser_*` 工具），不再调用不存在的 "health" 工具后恒报 not_configured。
- **D5 字段键位统一**：adapter 读取 client 的 `http_status`/`network_summary`
  （原读 `status_code`/`api_requests` 恒空）；observation 落库键名不变。
- **D6 部分采集失败留痕**：console/network/screenshot 失败记入
  `partial_collection_errors` 并写日志，不再静默吞。
- **D7 截图落 `<task-root>/reports/<task-id>/screenshots/`**（MCP 进程 cwd），
  文件名带时间戳防覆盖。
- **D8 scope 判定统一**：adapter validate 优先用 scope_payload 走
  `Scope.is_in_scope`（与 runner 同一实现，全通配 scope 不再被 adapter 误拦）。
- **D9 限速默认接线**：新 `pacing.py`，默认 ct 2s / rdap 1s（其余 0）注入全部
  7 个 ExecutionContext 构造点；env `SURFACE_INTEL_MIN_INTERVAL_<ADAPTER>`
  覆盖，非法值 fail-closed。
- **D10 integrity 噪声修正**：无 action 行的任务（真实 recon）不再因 action
  维度报 unknown；`compute_integrity_status` 增 `total_actions` 判据。
- **D11 baseline 不再破坏续跑**：`latest_checkpoint` 排除 baseline phase；
  新 `diff_checkpoints` 支持任意两次 checkpoint 对比，`baseline-diff` 增
  `--baseline/--against/--output`。
- **D12 预算措辞修正**：README/OPERATIONS 明确预算机制 opt-in、默认未接线。
- **D13 委派标注预留**：delegation 模块标注"平台预留 API"；coordinator 的
  裸 SQL INSERT 改调 `delegation.create_subtask`。
- **D14 plan/run 降级标注**：文档明确其为 Mock 离线演示闭环，生产路径是 recon。
- **删除死代码**：`create --input` 死旗标、`IntegrityStatus.BOUNDED` 死枚举、
  migrations/ 纯注释 stub、零引用的 `schemas/scope.schema.json`（权威 scope
  格式以 `create` 内置校验为准）。
- **版本单源化**：`__version__ = "0.2.0"`，pyproject 与 4 处 UA/clientInfo
  全部引用单一来源，测试锁定一致。

## 0.1.0

- 初始化独立项目和 Python/SQLite 基础设施。
- 增加 schema version 1、任务范围快照、状态枚举、脱敏日志和离线 CLI。
- 增加 P1-P3 范围标准化、动作计划、Mock/FOFA 适配器、观察/证据/资产/缺口及报告重建。
- 增加 P4 能力矩阵、安全主动适配器、架构门控，以及 P5 checkpoint 和未知终态恢复。
- 增加 P6 子任务委派恢复、lease/heartbeat、幂等结果提交和 Playwright 外部适配器。
- schema version 更新至 3，记录 P6 checkpoint。
- 增加 P6 后恢复硬化：FOFA checkpoint 续跑、动作终态、暂停/取消停止、报告恢复摘要和运行模式参数。
- 增加最终验收质量门和重复运行幂等测试，防止不完整任务误报 `complete`。
- R2: 增加 FOFA 查询计划、拆分批次元数据、F 点覆盖摘要和真实任务状态推进；
  完成授权域名的被动真实 smoke test，当前能力标记为 bounded。
