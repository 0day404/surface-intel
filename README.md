# surface-intel

`surface-intel` 是一个授权范围内的攻击面情报编排器。当前已提供离线 P0-P3
最小闭环：

- `init`、`create`、`preflight`、`plan`、`run`、`status`、`validate`、`report`
  （`plan`/`run` 是 Mock 适配器驱动的离线演示闭环，生产路径是 `recon` 与各真实入口，见下）；
- 范围标准化、排除项优先、动作幂等、Mock 被动/FOFA 多页适配器；
- SQLite 观察/证据/资产/缺口索引，以及可重建 JSON/XLSX。
- P4 安全主动门控、能力矩阵、Safe HTTP/Port 适配器和 P5 checkpoint/崩溃恢复。
- P6 子任务租约/心跳/重复结果保护，以及外部 Playwright MCP 公开页面观察边界。

P6 的 Playwright 连接通过注入的 MCP 调用边界测试；项目不复制 Playwright 源码、
`node_modules`、浏览器缓存或凭据。

`run` 支持 `--mode serial|delegated|hybrid`；三种模式共享同一持久化状态和安全门控。
恢复时读取 checkpoint，未知外部终态不会被自动重放。**注意**：`plan`/`run` 是离线
演示闭环（Mock 适配器，`planning.py` 固定 `mock_passive`），勿在生产任务上执行
`plan`——写入的 mock planned 行会让 `report` integrity 停在 `unknown`（有 action
行即受 action 维度约束；真实 recon 不写 action 行，不产生该噪声）。

当前真实数据源仍需单独配置和契约验证。适配器能力状态明确区分
`available`、`degraded`、`not_configured`、`unsupported` 和 `failed`；Mock
适配器只用于离线测试，不能证明真实 FOFA、DNS、CT、RDAP 或 Playwright 已接通。

真实 FOFA 入口是显式受控的：

```powershell
uv run surface-intel fofa-health --real
uv run surface-intel fofa-search --task-root .\task <task-id> --domain example.com --real --confirm-real
```

查询命令要求任务范围已确认、域名在授权范围内，并通过内置 fofa-mcp
子项目的 stdio MCP 进程调用（fofa-mcp 已 vendored 于本仓库 `mcp/fofa-mcp/`
目录，保持独立版本与测试；部署位置三级解析：env
`SURFACE_INTEL_FOFA_ROOT` > 本仓库内置 `mcp/fofa-mcp/` > 历史固定
路径 `E:\Work\Copilot\fofa-mcp`）；凭据只由 FOFA MCP 自己从其
受控环境（`mcp/fofa-mcp/.env`，git 已忽略）读取，不进入 `surface-intel` 参数、SQLite 或报告。未配置或连接失败时
返回 `not_configured`/失败状态，不伪造结果。

R3.1 起真实被动 DNS 入口同样显式受控：

```powershell
uv run surface-intel dns-health --real
uv run surface-intel dns-lookup --task-root .\task <task-id> --domain www.example.com --real --confirm-real
```

`dns-lookup` 要求任务范围已确认且域名在授权范围内；默认查询 A/AAAA/CNAME/NS/
MX/TXT/SOA（`--types` 可收窄，白名单外类型被拒绝）。每个记录类型是独立动作，
经统一执行器写入 `adapter_call` 审计（预算机制 opt-in、默认未接线；适配器间
限速由 `SURFACE_INTEL_MIN_INTERVAL_*` 默认接线，见 OPERATIONS R5）。解析出的
CNAME 目标、IP 和第三方域名只产生 `candidate` 观察，不会被递归解析或确认为资产。

R3.2 起真实被动 CT 入口同样显式受控：

```powershell
uv run surface-intel ct-health --real
uv run surface-intel ct-search --task-root .\task <task-id> --domain example.com --real --confirm-real
```

`ct-search` 查询证书透明度日志，保存 CN、SAN、issuer、serial、validity、
log source 和 first_seen/last_seen。证书 SAN 只产生 `candidate` 关系，任何
第三方或范围外域名都会把观察升级为 `needs_review`；CT 匹配单独绝不确认资产
归属。默认 `auto` 策略：crt.sh 主源，失败时自动转移到 ctlogs.dev（观察携带
`failover` 标记，来源可审计）；`SURFACE_INTEL_CT_SOURCE=crtsh|ctlogs` 可锁定
单一源。

R3.3 起真实被动注册数据入口同样显式受控：

```powershell
uv run surface-intel rdap-health --real
uv run surface-intel rdap-lookup --task-root .\task <task-id> --target 203.0.113.5 --kind ip --real --confirm-real
```

`rdap-lookup` 经 rdap.org（`SURFACE_INTEL_RDAP_ENDPOINT` 可覆盖）查询授权域名或
IP 的注册组织、网络、CIDR、abuse 联系和事件时间；TLD 无 RDAP 服务时回退
WHOIS（端口 43，只提取白名单字段），回退结果明确标记
`whois_fallback`/`whois_text_unverified`。注册数据只能产生 `candidate` 归属
关系——组织名或 ASN 相同绝不确认资产归属。ASN 直查需范围文档 `authorized_asns`
显式授权，或 R4 归并绑定（ASN 由授权目标自身的注册记录揭示）。

R4.1 起提供离线资产归并：`surface-intel merge <task-id> --task-root .\task`
把 DNS/CT/RDAP 观察幂等归并为统一资产模型（domain/ip/cidr/asn/organization、
强/中关联、来源 provenance、置信度、first/last seen 分层、冲突保留）。
归并纯本地计算，不发起网络请求；弱关联与归属类资产永不确认。

范围修订与监控基线：`scope-amend`（扩 IP/域名/主动授权/端口，`--confirm` 单事务
落快照并冻结旧版本动作）、`baseline-save` / `baseline-diff`（监控基线与任意两次
checkpoint 的资产差量对比，`--baseline/--against/--output` 可选）——详见
OPERATIONS.md。

P0 只提供离线项目骨架、
SQLite 状态、配置、状态枚举、脱敏日志以及 `init`、`create`、`status`、`validate`
命令；不会访问真实目标、FOFA 或 Playwright。

## 安装

本仓库由两层组成：根项目的 `surface-intel` 工具，与内置的 fofa-mcp 子项目
（`mcp/fofa-mcp/`）。两层各自有 `pyproject.toml` 与 `uv.lock`，需分别
`uv sync`；fofa-mcp 保持独立版本（`0.2.0`）与测试，随目录整体走，迁移时一并拷贝。

```bash
# 1. 根项目（surface-intel 工具本体，Python >= 3.11）
uv sync --extra dev

# 2. 内置 fofa-mcp 子项目（FOFA MCP 服务，独立 venv）
cd mcp/fofa-mcp && uv sync && cd ../..

# 3. FOFA 凭据：仅落在 fofa-mcp 子项目根目录的 .env（git 已忽略）
#    surface-intel 本体不持有、不读取 FOFA Key
```

- **环境**：Python ≥ 3.11、`uv`（依赖与虚拟环境管理）。工具本体依赖 Pydantic、
  openpyxl、dnspython；fofa-mcp 依赖 `httpx` / `mcp` / `python-dotenv`。
- **验证离线闭环**：

  ```bash
  uv run pytest          # 根项目离线测试
  cd mcp/fofa-mcp && uv run pytest   # fofa-mcp 子项目测试（fixture，不打真实 FOFA）
  ```

- **拉起项目骨架**：

  ```powershell
  uv run surface-intel init .\task
  ```

  未确认授权范围的任务会进入 `awaiting_scope`，`validate` 返回非零状态并禁止
  外部调用。生产任务建议用 `init --scaffold` 建项目目录骨架（详见 SKILL.md
  「目录规范」）。

两级 sync 完成后，`fofa-health --real` 会走内置 `mcp/fofa-mcp/` 副本读取其
`.env` 双账号，返回 `available`；未配置或 `.env` 缺 `FOFA_KEY` 时返回
`not_configured`，不伪造结果。

## MCP 接入

### fofa-mcp（内置子项目）

`surface-intel` 通过 `mcp/fofa-mcp/` 的独立 stdio MCP 进程调用 FOFA——本体只经
MCP 协议透传，不内置 FOFA 实现，不复制其源码/依赖/凭据。

- **位置解析**（`fofa_client._default_fofa_root()`，三级）：
  1. env `SURFACE_INTEL_FOFA_ROOT`（显式覆盖）；
  2. 本仓库内置 `mcp/fofa-mcp/`（以其中 `pyproject.toml` 为存在标志，默认命中）；
  3. 历史固定路径 `E:\Work\Copilot\fofa-mcp`（旧机兼容兜底）。
- **启动命令**：默认 `uv run fofa-mcp`（cwd 指向解析出的 fofa-mcp 根）；
  可 env `SURFACE_INTEL_FOFA_COMMAND` 覆盖。
- **凭据隔离**：FOFA Key 只由 fofa-mcp 自己从其受控环境读取
  （`mcp/fofa-mcp/.env`，git 忽略；可用 `FOFA_ENV_FILE` 指定其它文件，绝对路径
  按原路径、相对路径相对子项目根解析，进程 env 优先于文件同名项）。Key 不进入
  `surface-intel` 参数、SQLite、日志或报告。
- **工具**（7 个）：`fofa_health`（只查本地配置，不打远端）、`fofa_account_info`、
  `fofa_search`、`fofa_search_structured`、`fofa_search_next`、`fofa_stats`、
  `fofa_host`。多账号仅用于限流与可用容灾，不绕过 FOFA 权限。

给 Agent（Claude Code / 其它 stdio-MCP 宿主）挂接时，在工作项目（`[WORK_ROOT]`）
写 `.mcp.json`，把 fofa server 指到内置副本：

```json
{
  "mcpServers": {
    "fofa": {
      "command": "uv",
      "args": ["run", "--directory", "<SKILL_DIR>/mcp/fofa-mcp", "fofa-mcp"]
    }
  }
}
```

`<SKILL_DIR>` 为 skill 目录绝对路径（如 `E:/Work/AI/Skills/surface-intel`）。
多域复合 FOFA 查询（含内层引号）走该 server 直查（JSON 参数不经 shell），
避免 Windows shell 剥引号导致 FOFA 语法错。

### Playwright / 浏览器层（外部，非内置）

浏览器观察经外部 MCP，不在本仓库内：

- **公开页观察（工具证据链）**：`surface-intel browser-observe` 拉起
  `npx -y @playwright/mcp@latest --stdio`，把渲染面落 observation+evidence；
  未装 node/npx 时健康检查返回显式 `not_configured`。
- **深挖直驱（不进工具证据链）**：`chrome-devtools-mcp`，工作项目 `.mcp.json`
  建议带 `--headless --isolated --workspace=<项目>/task`：

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["-y", "chrome-devtools-mcp@latest", "--headless", "--isolated",
               "--workspace=E:/Work/Claude/<WORK_ROOT>/task"]
    }
  }
}
```

两层的调用序列、边界与版本注记见 SKILL.md「浏览器深挖层」。

## GitHub / 代码泄露面收集（gh 优先）

泄露面收集**优先 `gh` CLI**（PATH 主动发现：先 `where gh` 确认，不设绝对路径），
无 `gh` 时降级 curl 匿名低速（仓库/用户搜索可用、code search 需鉴权、~10
req/min）。仓库 / 用户 / 代码三类都搜，查询词三组：主域裸串 + `password`/
`secret`/`token`/`jdbc`/`.env`；组织名/RDAP org；`filename:.env`/`id_rsa` 类组合。

```bash
gh search code '"example.com" password' --limit 20 --json repository,path,htmlUrl
gh search code '"example.com" jdbc'   --limit 20 --json repository,path,htmlUrl
gh search repos  '目标org名' --limit 30 --json fullName,description,updatedAt
gh search users  '目标org名' --limit 20 --json login,type,bio
```

判读：内部端点、邮箱→人名映射、误提交配置（`.env`/kubeconfig/私有仓库串）。
**只记录存在性（html_url + path），不拉取内容、不试用任何凭据**；命中入发现集
（来源=`github`），敏感度高的直接进风险表 P1-P2。

国内补面走 **Gitee**（GitHub 搜不到的自建/镜像仓库），三通道按稳定性排序：
API v5（稳定，`curl "https://gitee.com/api/v5/search/repositories?q=组织名&per_page=20"`，
Issue/仓库/用户同型）、网页代码搜索（脆弱、限频，仅轻量；失败退回 API）、
官方 code API（预留）。判读同 GitHub：只记存在性（url+path），不拉内容、不试
凭据，命中来源=`gitee`。详见 SKILL.md「GitHub 泄露面 / Gitee 泄露面」。

## 报告输出

产物固定落在 task 目录的 `reports/` 下（`init --scaffold` 建的项目结构里，
`task-root` 为 `[WORK_ROOT]/projects/<项目>/task`）：

```
<task-root>/
└── reports/
    ├── <task-id>/            # 每个任务一个子目录（永不跨任务覆盖）
    │   ├── result.json       # 机器可读权威产物（默认唯一中间产物）
    │   ├── result.xlsx       # 人工查底稿（opt-in，--xlsx）
    │   ├── analyst_report.xlsx# 13 张公司交付模板（opt-in，--xlsx）
    │   ├── screenshots/       # 浏览器观察截图（observation-*.png）
    │   └── diff-report-<ts>.md# 差量复查 markdown 一页（diff-report 命令）
    └── merged/
        └── <code>/           # 多任务合并（--merge），<code>=8 位确定性指纹
            ├── merged_report.xlsx
            └── manifest.json
```

- **`report` 默认 json-only**：`uv run surface-intel report <task-id> --task-root <task-root>`
  唯一中间产物是 `result.json`——AI/程序消费的权威格式，也是 5-sheet 交付
  工作簿的数据源。xlsx 渲染（`result.xlsx` / `analyst_report.xlsx`）仅为人工查
  底稿的 opt-in（`--xlsx`），不作任何交付件（2026-10-05 操作方裁定）。
- **最终交付工作簿**（5 张定序 sheet）由通用脚本从 `result.json` 生成，直接落
  项目 `reports/`，文件名 `R<轮次>_<日期>_<主题>.xlsx`：

  ```bash
  uv run --with openpyxl python [SKILL_DIR]/scripts/build_deliverable_xlsx.py \
    --result-json <task-root>/reports/<task-id>/result.json \
    --out [WORK_ROOT]/projects/<项目>/reports/R1_20261005_<主题>.xlsx
  ```

  5 张 sheet：报告摘要 / 域名资产 / IP·网段·基础设施 / 风险点 / 站点与服务；
  深色表头白字、冻结首行、自动筛选、风险 P1-P4 分级着色与归属判定着色。脚本内
  发现集/风险/归属列按项目定制（随项目替换，含 ClientCorp R2 实战样例数据），
  证据列（HTTP 状态/响应头/最终 URL）从 `result.json` 自动读。
- **多任务合并**：`report --merge <task-id>... --task-root <task-root>` 生成跨任务
  合并工作簿（5 张：报告摘要/域名资产/IP资产/站点/服务），落 `reports/merged/<code>/`，
  同名集合幂等复用目录；不跨任务合并归属判定，仅并列呈现。
- **`result.json` 主要键**：`report_version`、`task`、`integrity_status`、`summary`
  （资产/观察/证据/冲突/review 计数 + budget）、`scope_authorization`、
  `adapter_status`、`budgets`、`coverage`、`quality`（gaps + review_queue）、
  `merge_summary`、`assets`/`services`/`domains_certificates`/`certificates`/
  `web_stack`/`asset_relations`/`asset_conflicts`、`browser`（pages + network）、
  `observations`、`evidence`、`events`、`checkpoints`、`fofa`（plans/batches/
  coverage）、`snapshot_diff`、`recovery_summary`、`export`。
- **脱敏**：`result.json` 与 xlsx 默认保留邮箱占位符；显式 `--reveal-emails`
  才把 `redaction_map` 明文回填进交付件（合并报告同受此开关控制）。
- **xlsx 公式注入防护**：所有写 xlsx 的单元格经 `_excel_safe` 转义，防止
  `=…`/`+…` 类公式注入。

差量监控：`baseline-save` 存基线 checkpoint，`baseline-diff`（`--baseline/--against`
可指定任意两次 checkpoint）出资产差量 JSON；`diff-report` 把差量渲染成
「新出现/消失/变化/重现」markdown 一页落 `reports/<task-id>/`（全量列出、不截断），
作为从一次性收集走向资产监控的桥。

## 子项目与修改审批

FOFA 仅通过内置适配器引用 `mcp/fofa-mcp/` 子项目（位置解析见上文「MCP 接入」）；
子项目保持自身版本与测试，改动遵循 MCP 修改审批规则。
