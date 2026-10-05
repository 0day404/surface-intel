#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""build_deliverable_xlsx.py — 交付工作簿生成(5 sheet;本文件含 CeramTec R2 实战样例数据)
数据源: --result-json 指向 report 产物 result.json (observation/evidence 证据列)
       + 发现集/风险/归属等代理分析列(本脚本内按项目定制,随项目替换)
规格: SKILL.md Phase 4 — 5 sheet 定序, 表头深色白字/冻结首行/自动筛选/条件着色
运行: uv run --with openpyxl python [SKILL_DIR]/scripts/build_deliverable_xlsx.py \
        --result-json [WORK_ROOT]/projects/<p>/task/reports/<task-id>/result.json \
        --out [WORK_ROOT]/projects/<p>/reports/<轮次>_<日期>_<主题>.xlsx
"""
import argparse
import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# 运行时由 --result-json / --out 覆盖(路径全参数化,零硬编码)
RESULT: Path = Path("result.json")
OUT: Path = Path("deliverable.xlsx")

# ---- 样式常量 ----
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
RISK_FILL = {"P1": ("FFC7CE", "9C0006"), "P2": ("FCE4D6", "C55A11"),
             "P3": ("FFF2CC", "BF8F00"), "P4": ("D9D9D9", "595959")}
OWN_FILL = {"自有": ("C6EFCE", "006100"), "关联第三方": ("FCE4D6", "C55A11"),
            "第三方": ("D9D9D9", "595959"), "待确认": ("FFEB9C", "9C6500")}
THIN = Side(style="thin", color="B0B0B0")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(vertical="top", wrap_text=True)
TAB = {"摘要": "1F4E79", "域名": "70AD47", "IP": "ED7D31", "风险": "C00000", "站点": "7030A0"}


def style_sheet(ws, header_row=1, widths=None, tab_color=None):
    for c in ws[header_row]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(vertical="center", horizontal="center")
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    ws.auto_filter.ref = ws.dimensions
    if widths:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=header_row + 1):
        for c in row:
            c.border, c.alignment = BORDER, WRAP
    if tab_color:
        ws.sheet_properties.tabColor = tab_color


def fill_cell(cell, text, color_map=None):
    cell.value = text
    if color_map and text in color_map and color_map[text]:
        bg, fg = color_map[text]
        cell.fill = PatternFill("solid", fgColor=bg)
        cell.font = Font(color=fg, bold=(text in ("P1", "P2")))


def load_evidence():
    d = json.loads(RESULT.read_text(encoding="utf-8"))
    ev = {}
    for o in d.get("observations", []):
        p = json.loads(o.get("payload_json", "{}"))
        src, t = o.get("source"), o.get("target")
        if src == "safe_http":
            rc = p.get("redirect_chain") or []
            h = p.get("headers", {})
            ev[t] = {
                "status": (rc[-1].get("http_status") if rc else None) or p.get("http_status"),
                "server": h.get("server"), "ctype": p.get("content_type"),
                "err": p.get("error_type"), "final": p.get("final_url"),
                "sec": " ".join(f"{k}:{h[k]}" for k in
                                ("strict-transport-security", "content-security-policy",
                                 "x-frame-options", "via", "x-powered-by") if h.get(k)),
            }
        elif src == "playwright_mcp":
            ev.setdefault(t, {})["title"] = p.get("title")
            ev[t]["final_url"] = p.get("final_url")
    return ev


# ---- Sheet 1: 报告摘要 ----
def sheet_summary(wb, ev):
    ws = wb.active
    ws.title = "报告摘要"
    rows = [
        ("任务", "苏州赛琅泰克高技术陶瓷有限公司（CeramTec 中国区）资产收集 — 复查轮 + 工具证据链补全"),
        ("主域名", "ceramtec-group.cn（家族: ceramtec.com.cn / ceramtec-group.com.cn / ceramtec-medical.cn / ceramtec.cn）"),
        ("判定口径", "凡归属/服务于 CeramTec 中国区的域名、IP、站点均纳入; 集团全球资产仅作背景(见 ceramtec-fofa-report.md)"),
        ("收集日期", "2026-10-05 (前轮: 2026-10-04 纯 FOFA 32 查询)"),
        ("数据源", "FOFA×20(MCP 双账号) + surface-intel 工具轮(DNS14/CT7/RDAP3/HTTP13/Playwright2) + CDX + curl + nslookup"),
        ("工具任务", "task-1545f597f89d4410 — integrity: complete, 9 assets / 35 observations / 35 evidence / 0 conflicts"),
        ("", ""),
        ("统计", "中国区自有域名 13+2 子域簇 / 核心 IP 3(郑州联通·苏州电信·阿里云) / 德国托管面 4 / 风险点 10(P1×2)"),
        ("", ""),
        ("覆盖说明", "FOFA 维度已饱和(domain/cert/icp/body/title/host 全维度, 20/20 预算); 双向迭代收敛(工具轮无新面揭示)"),
        ("覆盖缺口①", "数据库/RDP 端口未主动复核(权限门控合规) — 端口状态为 FOFA 快照 2026-09-26/27, 标注于风险表"),
        ("覆盖缺口②", "GitHub 泄露面未执行(gh CLI 未安装)"),
        ("覆盖缺口③", "ICP 备案主体 FOFA 库查无记录 — 建议人工经 beian.miit.gov.cn 核验"),
        ("覆盖缺口④", "泛微/Matomo 具体版本号未获(登录页不暴露; 版本接口探测超出 GET 级授权)"),
        ("", ""),
        ("已修正判断①", "bpm:8899 昨日报'WVS 可疑' → 本次确证为泛微 e-cology(eclology_JSessionid cookie + /wui/ 落点 + FOFA 产品指纹), 且含 Log4j2 组件"),
        ("已修正判断②", "官网服务器 116.255.206.92 画像补全: 昨日报仅记 SSH 10022, 本次 FOFA 画像揭示 MySQL(3306)/MSSQL(1433)/Oracle(1521)/RDP(33890) + Windows Server 2012(EOL) + TYPO3 CMS"),
        ("已修正判断③", "58.210.136.131:8601 二维码系统状态变化: FOFA 历史可见 → 本次 http-observe/curl 实测 timeout 不可达(疑似下线或白名单)"),
        ("已修正判断④", "工具轮 tls_error 根因: 301 跳转域证书不覆盖裸域(SSLCertVerificationError), 非服务异常; pre/dev 为 SSLEOFError(德国段拒绝握手)"),
        ("", ""),
        ("交付物", "本工作簿 + ceramtec-china-asset-report.md(风险编号两件互相对照)"),
    ]
    for r in rows:
        ws.append(r)
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 110
    for row in ws.iter_rows(min_row=1):
        row[0].font = Font(bold=True, color="1F4E79")
        row[1].alignment = WRAP
        row[0].border = row[1].border = BORDER
    ws.sheet_properties.tabColor = TAB["摘要"]


# ---- Sheet 2: 域名资产 ----
DOMAINS = [
    # 域名, 区域, 解析/指向, 归属判定, 关系类型, 依据, 来源
    ("ceramtec-group.cn", "中国", "116.255.206.92", "自有", "官网主域", "官网 title +苏州业务系统同主体佐证; NS=webercloud-china×3+ns.murrelektronik.com.cn(共享NS服务)", "fofa+dns"),
    ("www.ceramtec-group.cn", "中国", "116.255.206.92", "自有", "官网 www", "TYPO3 CMS 中文站(/zh/), nginx", "fofa+http"),
    ("tracking.ceramtec-group.cn", "中国", "116.255.206.92", "自有", "统计分析后台", "Matomo 登录页(公网), 安全头完善", "fofa+http"),
    ("ceramtec.com.cn", "中国", "116.255.206.92 (301→group.cn)", "自有", "老域名(301 归拢)", "NS=DNSPod(zinc/sturgeon), MX=M365 直证; CDX 存 2013 时代老站与 App 下载入口", "fofa+dns+cdx"),
    ("bpm.ceramtec.com.cn", "中国", "58.210.136.131:8899", "自有", "OA/BPM 业务系统", "泛微 e-cology(cookie+落点+指纹), Log4j2 组件", "fofa+http"),
    ("biolox.ceramtec.com.cn", "中国", "47.98.232.209 (阿里云)", "自有", "BIOLOX 品牌站(失效)", "403 Beaver 备案拦截页; 同机第三方 ichangzuo.com(cPanel) 共享虚拟主机", "fofa+http"),
    ("dev/pre.ceramtec.com.cn", "中国域·德国托管", "194.245.150.102", "自有", "开发/预发环境", "Apache 401; TLS 握手被拒(SSLEOF)", "fofa+http"),
    ("autodiscover.ceramtec.com.cn", "全球(M365)", "52.97.97.88", "自有", "邮箱自动发现", "Microsoft-HTTPAPI; MX=ceramtec-com-cn.mail.protection.outlook.com", "fofa+dns"),
    ("ceramtec-group.com.cn", "中国", "116.255.206.92", "自有", "防御性注册", "301 + 451; 直 https 证书报错(证书仅覆盖主域)", "fofa+http"),
    ("ceramtec-medical.cn (+www)", "中国域·德国托管", "91.132.200.61 (weber.digital 独占)", "自有", "医疗产品站", "TYPO3 中文站(/zh/); CT 4 证书; 托管商独占承载", "fofa+ct+http"),
    ("ceramtec.cn", "中国域·德国托管", "194.245.150.106 (301→com.cn→group.cn)", "自有", "中国域名(301 归拢)", "完整跳转链 curl 实证", "fofa+http"),
    ("5b84c834.ceramtec-group.cn", "德国", "91.132.200.52 (weber.digital)", "待确认", "泛解析/主机商验证子域", "451 合规屏蔽; 随机 hash 子域样式", "fofa+http"),
    ("cn-group/cn-medical.ceramtec.avenit-prod.de", "德国(Hetzner)", "49.13.251.12", "关联第三方", "建站代理商环境", "中国站生产数据副本存于代理商服务器 — 供应链面", "前轮报告+fofa"),
    ("ns2.webercloud-china.com / ns3.webercloud-china.de / ns4.webercloud.de", "DNS 托管", "weber.digital(集团代理商)", "第三方", "权威 DNS", "SOA=domain.weber.cloud", "dns"),
    ("ns.murrelektronik.com.cn", "DNS 托管", "murrelektronik(穆尔电子, 共享NS服务)", "第三方", "权威 DNS(共享)", "weber.digital 同客户生态", "dns"),
]

def sheet_domains(wb):
    ws = wb.create_sheet("域名资产")
    ws.append(["域名", "区域", "解析/指向", "归属判定", "关系类型", "判定依据", "来源"])
    for row in DOMAINS:
        ws.append(row)
    for r in range(2, ws.max_row + 1):
        fill_cell(ws.cell(r, 4), ws.cell(r, 4).value, OWN_FILL)
    style_sheet(ws, widths=[38, 14, 34, 12, 18, 60, 14], tab_color=TAB["域名"])


# ---- Sheet 3: IP·网段·基础设施 ----
IPS = [
    ("116.255.206.92", "中国·郑州", "AS4837 中国联通", "独占(公司主服务器)", "官网+Matomo+2 防御域; 画像: Win Server 2012+TYPO3+MySQL/MSSQL/Oracle/OpenSSH/RDP(33890); :80 server=infosec/1.0.0", "全库端口+远程管理公网暴露, OS 已 EOL — 见风险 R1/R9"),
    ("58.210.136.131", "中国·苏州", "中国电信(苏州IDC)", "独占(自建业务出口)", "泛微 OA:8899 + 二维码系统:8601(实测不可达)", "title 直接证实归属; :8601 状态变化见 R7"),
    ("47.98.232.209", "中国·阿里云", "AS37963 阿里云", "共享虚拟主机(同机 ichangzuo.com/cPanel)", "biolox.ceramtec.com.cn:80 + SSH:22 + MySQL:3306", "数据库+SSH 公网暴露 — 见 R3; 备案失效见 R4"),
    ("91.132.200.61", "德国", "AS35710 weber.digital GmbH", "独占托管(仅 medical.cn)", "ceramtec-medical.cn 官网(TYPO3)", "代理商独占管理, 状态健康"),
    ("91.132.200.52", "德国", "AS35710 weber.digital GmbH", "代理商段", "泛解析 451 面(5b84c834 等)", "OpenSSH:10022 对外; 同段 .61 同托管商"),
    ("194.245.150.102", "德国", "共享主机段(CSL 类)", "共享(前轮确认整段多租户)", "dev/pre.ceramtec.com.cn 测试环境", "段内大量无关企业, 勿整段划界"),
    ("194.245.150.106", "德国", "共享主机段", "共享", "ceramtec.cn/ceramtec.cz/ceramtec.co.uk(301 群)", "同前轮全球面"),
    ("49.13.251.12", "德国·Hetzner", "Hetzner", "代理商 avenit 生产机", "全球官网群 301 + cn-group/cn-medical 中国站副本", "供应链风险 — 见 R8"),
    ("52.97.97.88", "全球·Microsoft", "Microsoft 365", "SaaS", "autodiscover.ceramtec.com.cn", "邮箱体系 M365 — 配合 MX 直证"),
]

def sheet_ips(wb):
    ws = wb.create_sheet("IP·网段·基础设施")
    ws.append(["IP", "区域", "ASN/托管商", "多租户判定", "承载站点/画像", "关系洞察"])
    for row in IPS:
        ws.append(row)
    for r in range(2, ws.max_row + 1):
        v = ws.cell(r, 4).value
        if v and str(v).startswith("独占"):
            fill_cell(ws.cell(r, 4), "自有", OWN_FILL)
        elif v and ("共享" in str(v) or "代理商" in str(v) or "SaaS" in str(v)):
            fill_cell(ws.cell(r, 4), "第三方", OWN_FILL)
    style_sheet(ws, widths=[18, 12, 26, 30, 52, 40], tab_color=TAB["IP"])


# ---- Sheet 4: 风险点 ----
RISKS = [
    # 编号, 优先级, 资产, 发现, CVE线索, 依据, 建议, 状态
    ("R1", "P1", "116.255.206.92(官网服务器)", "数据库端口组公网暴露: MySQL(3306)+MSSQL(1433)+Oracle(1521), 外加 RDP(33890)+SSH(10022); Windows Server 2012 已 EOL", "Windows Server 2012 R2 后 EOL 积累补丁缺口(组件清单无专项条目); 数据库口令爆破面", "FOFA host 画像(快照 2026-09-27); 端口未主动复核(授权边界)", "立即核查防火墙策略, 数据库/远程管理端口收敛至内网或 VPN; 升级 OS; 轮转数据库口令", "待处置(端口状态为快照)"),
    ("R2", "P1", "bpm.ceramtec.com.cn:8899(泛微 e-cology)", "OA 系统公网明文 HTTP 暴露; 含 Log4j2 组件; 页面资源停留 2021-05(长期未更新); 缺 HSTS/CSP", "Log4j2→CVE-2021-44228 线索(版本未核); 泛微 BshServlet 反序列化(组件清单条目); e-office SQLi 族(清单待补号)", "工具轮响应包: cookie ecology_JSessionid+server WVS; playwright 落点 /wui/index.html; FOFA 产品指纹", "核查泛微版本与 Log4j 版本; /weaver/bsh.servlet.BshServlet 等历史路径自查; 升级+HTTPS+接入 WAF/限源", "待处置"),
    ("R3", "P2", "47.98.232.209(阿里云·biolox)", "MySQL(3306)+SSH(22) 公网开放, 承载 biolox 源站", "MySQL 弱口令/未授权面(版本未核)", "FOFA host 画像(快照 2026-09-26); 未主动复核", "安全组收敛 3306/22 至白名单; 与 R1 同步轮转口令", "待处置(端口状态为快照)"),
    ("R4", "P2", "biolox.ceramtec.com.cn", "ICP 备案失效(403 Beaver 拦截页), 源站仍存活且端口暴露", "合规风险(境内未备案对外提供服务的通报/关停风险)", "工具轮实测 403+FOFA 截图面", "重新备案或下线; 处置期间同步收敛源站端口", "待处置"),
    ("R5", "P2", "dev/pre.ceramtec.com.cn", "测试环境公网可达(德国托管), 仅 Basic Auth 单层防护, TLS 握手异常拒绝", "无专项 CVE; 测试环境常见旧依赖+弱凭据", "工具轮 SSLEOFError + 前轮 401 记录", "测试环境限源(公司出口/VPN); 若弃用即下线", "待处置"),
    ("R6", "P3", "tracking.ceramtec-group.cn(Matomo)", "统计后台公网可达", "无专项; 弱口令→流量/访客数据泄露", "工具轮: 安全头完善(CSP/HSTS/SameSite) — 配置良好降低风险", "限源或 VPN 化; 强制 MFA", "待处置(低)"),
    ("R7", "P3", "58.210.136.131:8601(二维码系统)", "状态变化: FOFA 历史可见(ASP.NET+OWNVIEW) → 本次实测 http/https 均 timeout", "无专项; 若为白名单仅对公网关闭, 内部暴露面仍在", "工具轮 http-observe timeout + curl 15s 超时复核", "确认服务现状: 若已下线同步注销解析; 若白名单化保持并审计白名单", "已变化(观察中)"),
    ("R8", "P3", "49.13.251.12(avenit 代理商)", "中国站生产数据副本(cn-group/cn-medical)存于代理商服务器", "供应链面(代理商失陷→中国站源码/数据)", "前轮全球面报告(2026-10-04)", "合同与数据面管控; 要求代理商隔离生产副本", "待处置(供应链)"),
    ("R9", "P3", "116.255.206.92(TYPO3 CMS)", "官网 CMS 为 TYPO3+PHP, 具体版本未核", "TYPO3 历史 CVE 族(版本核验前置)", "FOFA 产品指纹 TYPO3-CMS", " CMS 版本核查并纳入补丁管理", "待核验"),
    ("R10", "P4", "ceramtec.com.cn/ceramtec-group.com.cn/ceramtec.cn(裸域)", "裸域直连 https 证书报错(证书仅覆盖 www), 301/451 明文跳转", "无专项; 用户体验/降级面", "工具轮 SSLCertVerificationError 复现", "证书补 SAN 或关闭裸域直连", "低优先"),
]

def sheet_risks(wb):
    ws = wb.create_sheet("风险点")
    ws.append(["编号", "优先级", "资产", "发现", "CVE 线索(对组件清单核出)", "依据", "建议", "状态"])
    for row in RISKS:
        ws.append(row)
    for r in range(2, ws.max_row + 1):
        fill_cell(ws.cell(r, 2), ws.cell(r, 2).value, RISK_FILL)
        ws.cell(r, 1).font = Font(bold=True)
    style_sheet(ws, widths=[8, 9, 30, 48, 40, 34, 40, 16], tab_color=TAB["风险"])


# ---- Sheet 5: 站点与服务 ----
SITES = [
    # 宿主IP, 站点/服务, 端口, 类型, 管理面, HTTP状态, 返回头摘要, 备注(证据)
    ("116.255.206.92", "www.ceramtec-group.cn", "443/80", "官网", "否", "200", "nginx; text/html; final=/zh/; TYPO3+php", "工具轮 rt≈3.9s; body 256KB 截断"),
    ("116.255.206.92", "ceramtec-group.cn(裸域)", "443/80", "跳转", "否", "301", "nginx; →www", "直连 IP 443=451; :80 server=infosec/1.0.0"),
    ("116.255.206.92", "tracking.ceramtec-group.cn", "443/80", "分析后台", "是", "200", "nginx; X-Matomo-Request-Id; CSP+HSTS+SameSite 齐全", "curl 全包+playwright 双证据"),
    ("116.255.206.92", "MySQL/MSSQL/Oracle", "3306/1433/1521", "数据库", "是", "—", "FOFA 画像, 未主动复核", "R1"),
    ("116.255.206.92", "RDP/SSH", "33890/10022", "远程管理", "是", "—", "FOFA 画像(OpenSSH), 未主动复核", "R1"),
    ("58.210.136.131", "bpm.ceramtec.com.cn", "8899", "OA/泛微e-cology", "是", "200", "server=WVS; ecology_JSessionid; Last-Modified 2021-05-06; 无 HSTS/CSP", "curl 全包+playwright(落点/wui/index.html#/?logintype=1)"),
    ("58.210.136.131", "二维码管理系统", "8601", "业务系统", "是", "timeout", "http-observe/curl 实测均超时", "FOFA 历史标题'苏州赛琅泰克二维码管理系统-登录'; R7"),
    ("47.98.232.209", "biolox.ceramtec.com.cn", "80", "品牌站(拦截)", "否", "403", "server=Beaver; Non-compliance ICP Filing 页", "工具轮实测; R4"),
    ("47.98.232.209", "MySQL/SSH", "3306/22", "数据库/管理", "是", "—", "FOFA 画像, 未主动复核", "R3"),
    ("91.132.200.61", "www.ceramtec-medical.cn", "443/80", "官网(医疗)", "否", "200", "nginx; final=/zh/; TYPO3", "weber.digital 独占托管"),
    ("91.132.200.52", "5b84c834.ceramtec-group.cn", "443/80", "泛解析面", "否", "451", "nginx; Unavailable for Legal Reasons", "weber.digital 统一 451"),
    ("194.245.150.102", "dev/pre.ceramtec.com.cn", "80", "测试环境", "是", "401*", "Apache; Basic Auth; TLS 握手被拒(https)", "*401 为前轮记录; SSLEOFError 本轮复现; R5"),
    ("194.245.150.106", "ceramtec.cn(→www.ceramtec.com.cn→group.cn)", "80/443", "跳转", "否", "301", "Apache; charset=iso-8859-1", "curl 全链实证"),
    ("116.255.206.92", "ceramtec.com.cn / ceramtec-group.com.cn", "80/443", "跳转/防御域", "否", "301/451", "nginx; 裸域 https 证书报错", "R10"),
    ("52.97.97.88", "autodiscover.ceramtec.com.cn", "80", "邮箱自动发现", "否", "—", "Microsoft-HTTPAPI/2.0", "M365; MX 同证"),
    ("49.13.251.12", "cn-group/cn-medical.ceramtec.avenit-prod.de", "443", "代理商环境(中国站副本)", "是", "redirect_blocked", "Apache; 工具拦截第三方跳转", "供应链面; R8"),
]

def sheet_sites(wb, ev):
    ws = wb.create_sheet("站点与服务")
    ws.append(["宿主 IP", "站点/服务", "端口", "类型", "管理面", "HTTP 状态", "返回头摘要", "证据/备注"])
    for row in SITES:
        ws.append(row)
    for r in range(2, ws.max_row + 1):
        fill_cell(ws.cell(r, 5), ws.cell(r, 5).value, {"是": ("FCE4D6", "C55A11"), "否": None})
        st = str(ws.cell(r, 6).value or "")
        if st.startswith("2"):
            ws.cell(r, 6).fill = PatternFill("solid", fgColor="C6EFCE")
        elif st.startswith(("4", "5", "t")):
            ws.cell(r, 6).fill = PatternFill("solid", fgColor="FFC7CE")
    style_sheet(ws, widths=[18, 38, 12, 20, 9, 11, 52, 44], tab_color=TAB["站点"])


def main():
    global RESULT, OUT
    parser = argparse.ArgumentParser(description="交付 xlsx 工作簿生成(5 sheet)")
    parser.add_argument("--result-json", required=True, help="report 产物 result.json(证据列数据源)")
    parser.add_argument("--out", required=True, help="输出 xlsx 路径")
    args = parser.parse_args()
    RESULT = Path(args.result_json)
    OUT = Path(args.out)
    ev = load_evidence()
    wb = Workbook()
    sheet_summary(wb, ev)
    sheet_domains(wb)
    sheet_ips(wb)
    sheet_risks(wb)
    sheet_sites(wb, ev)
    wb.save(OUT)
    print(f"saved: {OUT}")


if __name__ == "__main__":
    main()
