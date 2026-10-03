# Coverage matrix

# surface-intel 覆盖矩阵

状态含义：`implemented` 表示有代码和测试；`contract_only` 表示有接口/边界但
没有真实外部运行时；`mock_only` 表示仅离线 fixture；`not_configured` 表示
外部能力尚未配置；`planned` 表示尚未实现。

| 规格 | 代码入口 | 测试入口 | 当前状态 | 真实能力备注 |
|---|---|---|---|---|
| S-00 统一数据模型 | `src/surface_intel/states.py`, `state/database.py` | `tests/unit`, `tests/recovery` | implemented | SQLite schema v3 |
| S-01 任务/范围/授权 | `policy/scope.py`, `cli.py` | `tests/unit`, `tests/security` | implemented | 真实目标仍须用户确认授权 |
| S-02 配置/日志/状态 | `config.py`, `logging.py` | `tests/unit` | implemented | 脱敏日志 |
| S-03 标准化/范围门控 | `normalization.py`, `policy/scope.py` | `tests/unit`, `tests/security` | implemented | 离线验证 |
| S-04 被动适配器 | `adapters/mock.py` | `tests/contract` | mock_only | DNS/CT/RDAP 尚未接入 |
| S-05 查询/动作计划 | `planning.py` | `tests/unit` | implemented | 输入文件消费仍需 R1 补齐 |
| S-06 FOFA MCP | `adapters/fofa.py` | `tests/contract` | contract_only | 未运行真实 MCP transport |
| S-07 架构识别/门控 | `policy/architecture.py` | `tests/security` | implemented | 识别流水线尚未完整 |
| S-08 条件主动探测 | `adapters/safe_http.py`, `safe_port.py` | `tests/security` | contract_only | 依赖注入 transport/connector |
| S-09 资产归并/可信度 | `coordinator.py` | `tests/integration` | implemented | 仍是最小归并模型 |
| S-10 缺口分析 | `coordinator.py` | `tests/integration` | implemented | 规则仍需扩展 |
| S-11 有界循环/停止 | `state/recovery.py` | `tests/recovery` | contract_only | 统一预算/速率尚未接入 |
| S-12 JSON 报告 | `reporting/export.py` | `tests/integration` | implemented | 可由持久化状态重建 |
| S-13 XLSX 报告 | `reporting/export.py` | `tests/integration` | implemented | 工作表仍未覆盖全部规格 |
| S-14 收集不变量/质量门 | `reporting/export.py` | `tests/unit`, `tests/integration` | implemented | 离线验收 |
| S-15 冲突/快照差异 | `coordinator.py` | `tests/integration` | planned | 当前仅有最小资产写入 |
| S-16 能力矩阵/安全预检 | `adapters/capabilities.py` | `tests/unit`, `tests/security` | implemented | R0 冻结状态契约 |
| S-17 暂停/恢复/重建 | `state/recovery.py`, `reporting/export.py` | `tests/recovery` | implemented | 真实 adapter session 未接入 |
| S-18 委派/租约/结果幂等 | `state/delegation.py` | `tests/recovery` | implemented | 无真实 Agent worker 平台 |
| Playwright 浏览器观察 | `adapters/playwright.py` | `tests/security` | contract_only | 外部 MCP 未启动验证 |

R0 不声称真实数据源可用；任何 `contract_only`、`mock_only`、`not_configured` 或
`planned` 能力必须在预检和报告中保留其降级状态。
