"""Analyst-facing report in the company delivery template (Chinese sheets).

Two-tier reporting: result.xlsx stays the machine-rebuildable evidence
artifact; analyst_report.xlsx follows the human delivery template - analytic
columns (归属档/用途/依据) are filled where the data supports them and left
as "—"/未核查 placeholders where human judgment is required. Formula-injection
protection and atomic publishing match the machine report.
"""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from ..state.database import Database

IN_SCOPE_TAG = "A-授权范围（待分析确认）"
RELATED_TAG = "B-关联待确认"
THIRD_PARTY_TAG = "C-第三方/共享基础设施"
DASH = "—"

HEADER_FONT = Font(bold=True)

_LOGIN_MARKERS = ("登录", "login", "管理", "admin", "后台", "sign in", "sso")


def _safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value if value is not None else DASH


def _sheet(wb: Workbook, name: str, header: list[str], rows: list[list]) -> None:
    ws = wb.create_sheet(name)
    ws.append(header)
    for cell in ws[1]:
        cell.font = HEADER_FONT
    for row in rows:
        ws.append([_safe(value) for value in row])


def _sources_of(payload_json: str | None) -> str:
    try:
        payload = json.loads(payload_json or "{}")
    except json.JSONDecodeError:
        return DASH
    source = payload.get("source")
    if source:
        return str(source)
    return DASH


def build_analyst_report(db: Database, task_id: str, report: dict, output_dir: Path) -> dict:
    """Build analyst_report.xlsx from the collected data; returns export metadata."""
    assets = report["assets"]
    observations = report["observations"]
    relations = report["asset_relations"]

    domains = {row["normalized_value"]: row for row in assets if row["asset_type"] == "domain"}
    ips = {row["normalized_value"]: row for row in assets if row["asset_type"] == "ip"}
    services = [row for row in assets if row["asset_type"] == "service"]

    # relation graph: ip -> domains (fofa_host_ip / service), host -> cert SANs
    asset_value = {row["asset_id"]: row["normalized_value"] for row in assets}
    ip_domains: dict[str, set[str]] = {}
    host_sans: dict[str, set[str]] = {}
    for relation in relations:
        src = asset_value.get(relation["from_asset_id"])
        dst = asset_value.get(relation["to_asset_id"])
        if not src or not dst:
            continue
        rtype = relation["relation_type"]
        if rtype in {"fofa_host_ip", "fofa_domain_ip"}:
            # relation direction is host -> ip; record the carried domain
            domain, ip = (src, dst) if dst in ips else (dst, src)
            if ip in ips:
                ip_domains.setdefault(ip, set()).add(domain)
        if rtype == "cert_name" and src in domains:
            host_sans.setdefault(src, set()).add(dst)

    def tag_for(normalized_value: str, asset_type: str) -> str:
        row = domains.get(normalized_value) or ips.get(normalized_value)
        if row is None:
            return RELATED_TAG
        if row["scope_status"] == "confirmed":
            return IN_SCOPE_TAG
        if row["architecture_status"] == "third_party":
            return THIRD_PARTY_TAG
        return RELATED_TAG

    # ---- 域名资产 ----
    domain_rows = []
    for value, row in sorted(domains.items()):
        subs = sorted(host_sans.get(value, set()))
        sources = sorted({item.get("source", DASH)
                          for item in json.loads(row.get("sources_json") or "[]")})
        domain_rows.append([
            value, tag_for(value, "domain"), DASH,
            ", ".join(subs) if subs else DASH,
            DASH,  # 归属依据 - analyst
            " + ".join(s.replace("_", " ") for s in sources) if sources else DASH,
        ])

    # ---- IP资产 ----
    rdap_country = _rdap_countries(observations)
    ip_rows = []
    service_values = [row["normalized_value"] for row in services]
    for value, row in sorted(ips.items()):
        port_set = sorted({item.split(":")[1].split("/")[0] for item in service_values
                           if item.split(":")[0] == value})
        carried = ", ".join(sorted(ip_domains.get(value, set()))) or DASH
        ip_rows.append([
            value, tag_for(value, "ip"), rdap_country.get(value, DASH),
            carried, ", ".join(port_set) if port_set else DASH, DASH,
        ])

    # ---- 站点（FOFA host + title）----
    site_rows = []
    seen_sites: set[str] = set()
    for row in observations:
        if row["source"] != "fofa_mcp":
            continue
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        host = str(payload.get("host", "") or "")
        if not host or host in seen_sites:
            continue
        seen_sites.add(host)
        title = str(payload.get("title", "") or DASH)
        scheme = str(payload.get("protocol", "http"))
        port = payload.get("port")
        prefix = "https" if str(port) == "443" or scheme == "https" else "http"
        site = host if host.startswith(("http://", "https://")) else f"{prefix}://{host}"
        site_rows.append([site, title, DASH])

    # ---- 服务 ----
    service_rows = []
    for row in sorted(services, key=lambda item: item["normalized_value"]):
        ip_part, _, rest = row["normalized_value"].partition(":")
        port, _, protocol = rest.partition("/")
        service_rows.append([ip_part, port, protocol.upper(),
                             tag_for(row["normalized_value"], "service"), DASH])

    # ---- 组件（FOFA product 声明，未验证）----
    component_rows = []
    seen_components: set[str] = set()
    for row in observations:
        if row["source"] != "fofa_mcp":
            continue
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        product = str(payload.get("product", "") or "").strip()
        if product and product.lower() not in {"unknown", "none"} and product.lower() not in seen_components:
            seen_components.add(product.lower())
            component_rows.append([product, "未知（未验证）", DASH, DASH,
                                   "FOFA 声明，需版本确认"])

    # ---- 登录入口（启发式：标题含登录标记）----
    login_rows = []
    for site, title, _ in site_rows:
        marker = next((m for m in _LOGIN_MARKERS if m in title.lower()), None)
        if marker:
            login_rows.append([site, IN_SCOPE_TAG if any(
                domain in site for domain in domains) else RELATED_TAG,
                "未验证（标题启发式）", marker, "待定", DASH])

    # ---- 信息泄露（最小规则：非 2xx 公网响应 + third_party 标记）----
    leak_rows = []
    for row in observations:
        if row["source"] != "safe_http":
            continue
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        status = payload.get("status_code")
        if status in {401, 403}:
            leak_rows.append([f"{payload.get('target')} 返回 {status}",
                              "safe_http", "公网环境暴露非公开响应，需确认是否应暴露"])
    for value, row in sorted(ips.items()):
        if row["architecture_status"] == "third_party":
            leak_rows.append([f"{value} 为第三方/共享基础设施",
                              "架构识别", "非自有资产，主动探测已阻止"])

    # ---- 占位节（调查结论式）----
    def placeholder(conclusion: str):
        return [[conclusion, DASH, DASH]]

    workbook = Workbook()
    summary = workbook.active
    summary.title = "报告摘要"
    summary.append(["task_id", "scope_version", "integrity_status",
                    "asset_count", "report_version"])
    for cell in summary[1]:
        cell.font = HEADER_FONT
    summary.append([task_id, report["task"]["scope_version"],
                    report["integrity_status"], len(assets),
                    report.get("report_version", DASH)])

    _sheet(workbook, "域名资产",
           ["域名资产", "归属档", "用途", "已发现子域", "归属依据", "数据来源"],
           domain_rows)
    _sheet(workbook, "IP资产",
           ["IP", "归属档", "位置/机房", "承载资产", "收录端口", "备注"],
           ip_rows)
    _sheet(workbook, "站点", ["站点", "标题/状态", "说明"], site_rows)
    _sheet(workbook, "服务",
           ["IP", "端口", "服务", "归属档", "说明"], service_rows)
    _sheet(workbook, "组件",
           ["组件", "版本", "出现位置", "归属档", "风险注释"],
           component_rows or [["未核查", DASH, DASH, DASH, "FOFA 未返回 product 字段"]])
    _sheet(workbook, "登录入口",
           ["URL", "归属档", "认证形态", "组件", "优先级", "备注"],
           login_rows or [["未核查", DASH, DASH, DASH, DASH, DASH]])
    _sheet(workbook, "APP资产",
           ["调查结论", "说明", "依据"], placeholder("未核查"))
    _sheet(workbook, "微信公众号资产",
           ["调查结论", "说明", "依据"], placeholder("未核查"))
    _sheet(workbook, "小程序",
           ["调查结论", "说明", "依据"], placeholder("未核查"))
    _sheet(workbook, "暗网泄露",
           ["状态", "说明", "建议"], placeholder("未核查（无泄露情报库渠道）"))
    _sheet(workbook, "信息泄露",
           ["泄露项", "来源", "说明"],
           leak_rows or [["未核查", DASH, DASH]])
    _sheet(workbook, "漏洞汇总",
           ["目标", "潜在漏洞", "编号", "等级", "状态", "依据"],
           [["未核查", "本工具不做漏洞结论——主动验证需单独授权", DASH, DASH, "未验证", DASH]])

    # atomic publish
    import os
    import tempfile

    handle = tempfile.NamedTemporaryFile(prefix="analyst-", suffix=".xlsx",
                                         dir=str(output_dir), delete=False)
    handle.close()
    temp_path = Path(handle.name)
    try:
        workbook.save(temp_path)
        os.replace(temp_path, output_dir / "analyst_report.xlsx")
        return {"status": "exported", "path": str(output_dir / "analyst_report.xlsx")}
    except Exception as exc:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            return {"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                    "path": str(output_dir / "analyst_report.xlsx")}


def _rdap_countries(observations: list[dict]) -> dict[str, str]:
    countries: dict[str, str] = {}
    for row in observations:
        if row["source"] != "rdap_lookup":
            continue
        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError:
            continue
        record = payload.get("record") or {}
        country = record.get("country")
        if country and payload.get("target"):
            countries[str(payload["target"])] = str(country)
    return countries
