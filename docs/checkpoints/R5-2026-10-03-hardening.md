# R5 checkpoint: budget / rate / concurrency / stop / recovery hardening

## 阶段

R5 全量增量（执行器强化 + 四级预算 + 停止/冻结/调度）。

## 目标

让长任务可控、可暂停、可恢复且不无限扩张：四级预算、速率/并发限制、
stop token、超时取消语义、scope 冻结与真实调度模式。

## 已完成

- **R5.1 执行器强化**（`execution.py`、`ratelimit.py`、`adapters/base.py`）：
  - `RateLimiter`：每键最小间隔节奏，键=`adapter:<name>` 与
    `target:<value>` 双独立时间线（每适配器与每目标同时受限）；时钟与
    sleeper 可注入（测试零真实等待）；负间隔拒绝。
  - `action_timeout`：`asyncio.wait_for` 包裹适配器执行；超时先调
    `adapter.cancel`——取消成功记 `external_state="cancelled"`，取消失败/
    不支持记 `"unknown"`（绝不假装知道外部终态）。适配器自抛 TimeoutError
    走同一取消路径。`AdapterResult` 新增 `external_state` 字段。
  - `max_consecutive_errors`：连续 failed/timed_out 计数达阈值置
    `context.stop_requested + stop_reason="consecutive_errors"`；completed
    归零。`_finish` 全路径携带 context。
- **R5.2 四级预算**（`state/budget.py`）：
  - `BudgetSpec`/`BudgetLedger`：task/phase/target/adapter 四级，budget 表
    kind=`level:key` 复用既有事务原语（BEGIN IMMEDIATE 原子 UPDATE）。
  - 原子多级预留：任一级无余量 → 整组回滚（partial release）返回 None；
  - 执行器集成：ledger 存在时为权威路径（按 action 的 phase/target/adapter
    匹配所有级）；已执行动作（failed/timed_out 也）提交消耗——保留事实，
    不伪造退回；blocked 在预留前拒绝、零消耗。
  - 并发竞争测试：4 线程抢最后 1 单位，恰好 1 赢、无超卖。
- **R5.3 停止/冻结/调度**（`coordinator.py` 重写、`state/recovery.py`）：
  - stop token：动作之间检查任务状态（paused/cancelled）与连续错误阈值；
    停止后不产生新分页/探测调用，返回 `status=paused|cancelled|
    consecutive_errors`。
  - `freeze_stale_actions`：scope 版本变化时冻结旧版本未完结动作
    （cancelled+`scope_version_frozen`+事件留痕）；已完成结果不动（历史
    快照）。
  - 调度模式真实化：serial 顺序；hybrid 以 `asyncio.Semaphore(concurrency)`
    并行被动动作（并发上限测试 ≤2）；delegated 按适配器分组创建幂等
    subtask 行（heartbeat 置位、状态机 planned→started→completed）再逐组
    执行。claim 竞争保护全模式保留。CLI `run --mode` 接通。

## 新增/修改文件

新增：`src/surface_intel/ratelimit.py`、
`tests/contract/test_execution_hardening.py`（6）、
`tests/integration/test_budget_ledger.py`（7）、
`tests/integration/test_r5_stop_and_scheduling.py`（6）。
修改：`src/surface_intel/execution.py`、`src/surface_intel/adapters/base.py`、
`src/surface_intel/state/budget.py`、`src/surface_intel/state/recovery.py`、
`src/surface_intel/coordinator.py`、`src/surface_intel/cli.py`（run --mode
接线）、OPERATIONS、REAL_COLLECTION_TASK_PLAN。

## 数据库/Schema 变化

无 migration（预算四级复用 budget 表 kind 前缀；subtask/lease 表沿用
migration 3）。SCHEMA_VERSION 仍为 11。

## 测试命令和结果

```powershell
cd E:\Work\Copilot\surface-intel
uv sync --extra dev
uv run pytest -q
```

结果：`284 passed`（265 + 19）。覆盖：速率节奏/双时间线/参数拒绝、执行器
限速前置、超时取消 cancelled、取消失败 unknown、连续错误置 stop、成功
归零、四级原子预留/commit、级间隔离、部分预留回滚、预算耗尽阻断、失败
保留消耗、并发竞争不超卖、非法 spec 拒绝、旧 scope 冻结（完结不动+事件）、
串行暂停停止、连续错误停止调度、hybrid 并发上限+全完成、delegated subtask
幂等+心跳、非法 mode 拒绝。

## 外部调用状态

全部 fixture/mock（离线编排 + 注入时钟），零真实网络；`fofa-mcp` 未触碰。

## 已知限制

- 自动重试退避未实现（现状：失败隔离 + 阈值停止 + 合法重跑）；建议真实
  流量需求出现后在 ledger 总量内实现指数退避。
- 字节/总时长/结果上限维度是 ledger 的自然扩展（units 语义已就绪），参数
  待真实流量标定。
- 在线 worker 的 lease 竞争（多进程租约抢占）留 R6 真实执行阶段；当前
  delegated 模式的 subtask 行与心跳已就绪。
- 衰减/速率/预算参数为初始冻结值，调整属策略变更需记录。

## 下一步

R5 完成（R6 前置条件：R4 架构门控 + R5 预算停止均已就绪）。进入 R6：
Safe HTTP / Safe Port / Playwright 真实观察——一切主动流量的架构门控、
预算门控与停止门控已在本轮就位。

## 是否可恢复

是。预算为 DB 事务态；停止为动作间检查点（幂等）；冻结为一次性标记；
调度无内存态依赖——进程退出后 recovery scan + checkpoint 语义不变。
