# R7 final checkpoint: full review and R2 bounded upgrade

## 阶段

最终验收（① 主动观察 smoke + ② FOFA 拆批真实覆盖 + ③ 完整评审与 R2 升级）。
**R0-R7 全部完成并通过真实数据验收。**

## 授权与边界

- 目标：`www.ceramtec-group.cn` / `ceramtec-group.cn`（用户确认的已授权
  渗透测试项目，2026-10-04 明确授权本轮 ①②③）
- ① 主动部分：safe_discovery、显式端口清单 [80, 443]、GET only
- ② 被动部分：FOFA 复合查询 size=10、会话预算护栏内
- 未执行：登录/状态变更/下载/表单提交（适配器契约拒绝）、范围外跟随

## ① 主动观察 smoke 结果（task-991edf08c1d54bbd）

链路：FOFA 被动（喂架构门控）→ merge → 架构门控 → 三类主动观察。

- HTTP：**completed**——200/ok，重定向 /zh/ 被跟踪并记录，content-type
  text/html，body 256KB 摘要（truncated=true 诚实标记），TLS 证书
  SHA-256 记录
- 端口：443 **open**（https 提示，probe 856ms，TLS 握手成功）、80 **open**
  （http，28ms）——均为显式清单端口，零范围扩展
- 浏览器：**not_configured: node_npx_not_available**——本机无 node/npx，
  按计划显式报告原因，不伪造成功
- 架构门控：本任务 FOFA 数据未触发阻断（third_party 空），观察正常放行

## ② FOFA 拆批真实覆盖结果（task-71bfd86499cb402c）

- 4110 字符复合查询强制拆批 → **2 个真实批次独立完成**（batch-1: 2 条、
  batch-2: 8 条），单批失败隔离语义未触发但路径已真实验证
- 真实新发现：`tracking.ceramtec-group.cn` 与
  `5b84c834.ceramtec-group.cn`（CDN/SaaS 验证型随机子域，解析到 EU IP
  91.132.200.52）——归并后 10 资产（4 domain confirmed + 2 ip candidate +
  4 service candidate）、2 third_party 架构标记
- 真实多页未触发：授权数据集不足一页（has_more=false）；分页机制
  （cursor/next_token）由 R2 契约测试覆盖——如实记录为限制
- Windows asyncio 管道关闭警告在 driver 直调场景复现（CLI 路径已修），
  不影响结果；记录为已知噪音

## ③ 完整评审与 R2 升级

升级条件核验（原 bounded 条款"R3-R7 限制、覆盖、预算、归并和主动观察
完成并通过验收"）：

| 条件 | 证据 |
|---|---|
| 限制 | scope 门控、架构阻断集+review item、方法/端口白名单、私网拒绝（R1/R4/R6 测试） |
| 覆盖 | 五类被动源 + 三类主动观察全部接入并有真实数据验证（R3/R6/本轮） |
| 预算 | BudgetLedger 四级原子预留 + FOFA F 点记账（R5.2 + 本轮 consumed=0） |
| 归并 | 统一资产模型 + 冲突保留 + 生命周期 + 衰减（R4，本轮真实数据归并验证） |
| 主动观察 | Safe HTTP/Port/Playwright 真实链路（R6 + 本轮 ①） |

**决定：R2 `bounded` → `accepted (2026-10-04)`**，AGENT_HANDOFF_PROMPT
状态表已更新（R3-R7 同步标记完成）。

## 本轮新增修复

- **scope 主动级别字符串旁路（安全级）**：from_dict 不转换
  active_testing_level，字符串 "passive_only" 与枚举比较永不相等，可使
  passive-only 范围通过主动检查。修复：__post_init__ 强制归一为枚举，
  未知值 fail-close 为 PASSIVE_ONLY。+2 安全回归测试。

## 测试命令和结果

```powershell
cd E:\Work\Copilot\surface-intel
uv sync --extra dev
uv run pytest -q
```

结果：`326 passed`（322 + 4 安全回归）。

## 真实数据资产发现摘要（可交付）

- 主站 www.ceramtec-group.cn @ 116.255.206.92（CN），HTTPS/443 + HTTP/80，
  重定向至 /zh/
- tracking.ceramtec-group.cn、5b84c834.ceramtec-group.cn @
  91.132.200.52（EU）——第三方追踪/验证类子域，已标记 third_party 待复核
- 全部归并数据可通过 `report` 命令重建（JSON 十六节 + XLSX 十六表）

## 下一步

R0-R7 全部完成并通过真实验收。后续为运营性使用：新授权目标按同一链路
执行；持续监控按 baseline-save/diff 循环；任何主动观察保持单次授权门控。
