# R4 checkpoint: passive architecture identification

## 阶段

R4.2 架构识别（R4 第二增量）。

## 目标

从合并资产与观察识别 CDN/WAF/LB/云托管/DNS 托管/邮件依赖，写
`architecture_status` 与逐信号证据；被动层绝不产出 confirmed_* 状态，分类
不触碰 scope 授权。

## 已完成

- **Migration 9**：`asset` 扩展 `architecture_json`（证据数组；v8→v9 原地
  ALTER，版本断言测试同步更新）。
- **识别模块**（`architecture.py`）：
  - 静态厂商签名：`VENDOR_SUFFIXES`（19 组域名后缀：cloudflare/akamai/
    cloudfront/fastly/cdn77/azure-cdn/imperva/aws-elb/azure-traffic-manager/
    aws/azure/google-cloud/route53/ultradns/registrar-dns/outlook/
    google-workspace 等，覆盖 cdn/waf/lb/cloud_hosting/dns/email 六类）+
    `ORG_KEYWORDS`（组织/签发者关键词）。精确后缀匹配——
    `evildnscdn.cloudflare.net.evil.io` 类后缀伪装不匹配（有测试）。
  - 信号来源：DNS CNAME/NS 目标后缀、RDAP 组织/注册商关键词、CT 证书签发者
    关键词；每条信号携带 observation_id + source，逐条可溯源。
  - 分级：单一信号 `{category}_candidate`；同类 ≥2 份独立信号
    `{category}_likely`（identical 去重——同一信号重复观察不算佐证）；多
    likely 类别按 cdn>waf>lb>cloud_hosting>dns>email 取优先；词表冻结于
    `ARCHITECTURE_STATUSES`，`confirmed_direct`/`confirmed_hosted`/
    `origin_candidate` 常量隔离、识别层绝不写入（R6 专用）。
  - `third_party`：scope candidate 且被 confirmed 资产关系指向的范围外
    资产；不改变 scope_status。
  - 幂等：重复 annotate 重写且字节级一致（证据不带派生时间戳）。
- **CLI**：`merge` 命令现在输出 `architecture_classified`/
  `architecture_third_party` 计数。

## 修复的缺陷（开发中发现）

1. **双重展开**：pass-1 已按 category 展开信号后，`classify_signals` 再次按
   `categories` 列表展开——单信号被误判为双份佐证（cdn_candidate 被抬成
   cdn_likely）。修复：有 `category` 字段用之，否则才展开 `categories`。
2. **锚点确认缺口（merger，R4.1 遗留）**：`scope_anchored=True` 无条件置
   confirmed——伪造/导入的越权目标观察可借锚点通道洗成 confirmed 资产。
   修复：锚点也必须通过 `scope.is_in_scope` 才获得 confirmed；否则按普通
   分类落 candidate。新增安全测试覆盖。

## 新增/修改文件

新增：`src/surface_intel/architecture.py`、
`tests/contract/test_architecture.py`（9）、
`tests/integration/test_architecture_runner.py`（7）、
`tests/security/test_architecture_scope.py`（4）。
修改：`src/surface_intel/state/database.py`（migration 9）、
`src/surface_intel/merger.py`(锚点防御修复)、`src/surface_intel/cli.py`
（merge 集成架构识别）、`tests/contract/test_runtime_schema.py`、
`tests/unit/test_p0.py`（版本 9）、README（未改，OPERATIONS 已含）、
ADAPTERS、OPERATIONS、SCHEMA、REAL_COLLECTION_TASK_PLAN。

## 数据库/Schema 变化

SCHEMA_VERSION 8→9（`asset.architecture_json`）。

## 测试命令和结果

```powershell
cd E:\Work\Copilot\surface-intel
uv sync --extra dev
uv run pytest -q
```

结果：`247 passed`（220 + 27：合约 9、集成 7、安全 4、版本/迁移断言更新）。
覆盖：后缀/关键词匹配与伪装拒绝、单信号 candidate、佐证 likely、identical
不佐证、类别优先级、confirmed 永不产出、词表冻结、CDN likely 端到端
（证据引用 observation id）、DNS candidate、云托管 org、third_party 标记、
unknown 保持、注解幂等字节一致、重型信号仍不 confirmed、分类不改 scope、
third_party 不确认、证据必带引用。

## 外部调用状态

纯本地计算，零网络请求；`fofa-mcp` 未触碰。

## 已知限制

- 厂商签名为启发式种子：识别新厂商需扩签名表；未识别的 CDN 不会误报
  （落 unknown/candidate），但会漏报。
- 反向代理/Web/API/JS 指纹需主动观察（R6）。
- `origin_candidate`（CDN 背后源站推断）需主动验证数据，R6 接入。
- `*_likely` 与计划 `shared_infrastructure` 的对应关系已文档化；探测门控
  （only confirmed_* 可探测）在 R6 实现。

## 下一步

R4.3：以归并关系为绑定依据放开 RDAP ASN 直查（`asset_relation` 中存在
confirmed 资产到 asn 资产的 rdap_asn 关系时允许 `rdap-lookup --kind asn`）；
随后 R4 收尾核验（归并质量验收项）。

## 是否可恢复

是。架构识别为派生数据幂等重算（证据字节级一致），无外部状态。
