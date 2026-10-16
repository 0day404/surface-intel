#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""build_deliverable_xlsx.py — 交付工作簿生成(5 sheet;数据列按项目定制)
数据源: --result-json 指向 report 产物 result.json (observation/evidence 证据列)
       + 发现集/风险/归属等代理分析列(下方占位示例,随项目整体替换)
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


# ---- Sheet 1: 报告摘要(占位行:任务/口径/日期/数据源/统计/缺口/交付物按项目填写) ----
def sheet_summary(wb, ev):
    ws = wb.active
    ws.title = "报告摘要"
    rows = [
        ("任务", "<项目名> 资产收集 — <轮次主题>(占位,按项目填写)"),
        ("主域名", "<主域>(家族: <变体域列表>)"),
        ("判定口径", "<归属/服务判定口径>"),
        ("收集日期", "<YYYY-MM-DD>"),
        ("数据源", "<FOFA×N + 工具轮 + CDX/curl/nslookup 等>"),
        ("工具任务", "<task-id> — integrity: <status>, <N> assets / <N> observations"),
        ("", ""),
        ("统计", "<域名/IP/风险点计数>"),
        ("", ""),
        ("覆盖缺口", "<未覆盖维度与原因(权限门控/工具不可用/授权边界)>"),
        ("", ""),
        ("已修正判断", "<前后轮判断差异(可选)>"),
        ("", ""),
        ("交付物", "本工作簿 + <报告>.md(风险编号互相对照)"),
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


# ---- Sheet 2: 域名资产(占位示例:整体替换为项目发现集) ----
DOMAINS = [
    # 域名, 区域, 解析/指向, 归属判定, 关系类型, 依据, 来源
    ("www.example.com", "示例", "192.0.2.1", "自有", "官网主域", "<判定依据>", "fofa+dns"),
    ("mail.example.com", "示例", "192.0.2.2", "待确认", "邮箱服务", "<判定依据>", "fofa+dns"),
]

def sheet_domains(wb):
    ws = wb.create_sheet("域名资产")
    ws.append(["域名", "区域", "解析/指向", "归属判定", "关系类型", "判定依据", "来源"])
    for row in DOMAINS:
        ws.append(row)
    for r in range(2, ws.max_row + 1):
        fill_cell(ws.cell(r, 4), ws.cell(r, 4).value, OWN_FILL)
    style_sheet(ws, widths=[38, 14, 34, 12, 18, 60, 14], tab_color=TAB["域名"])


# ---- Sheet 3: IP·网段·基础设施(占位示例) ----
IPS = [
    # IP, 区域, ASN/托管商, 多租户判定, 承载站点/画像, 关系洞察
    ("192.0.2.1", "示例", "AS0 <ISP>", "独占(<定位>)", "<站点/画像>", "<洞察>"),
    ("198.51.100.1", "示例", "AS0 <托管商>", "共享(<说明>)", "<站点/画像>", "<洞察>"),
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


# ---- Sheet 4: 风险点(2026-10-05 操作方裁定: 不设"建议"列, 处置动作走 md 叙事) ----
RISKS = [
    # 编号, 优先级, 资产, 发现, CVE线索, 依据, 状态
    ("R1", "P1", "<资产>", "<发现描述>", "<CVE 线索(对组件清单核出)>", "<依据/证据>", "待处置"),
    ("R2", "P2", "<资产>", "<发现描述>", "<CVE 线索>", "<依据/证据>", "待处置"),
]

def sheet_risks(wb):
    ws = wb.create_sheet("风险点")
    ws.append(["编号", "优先级", "资产", "发现", "CVE 线索(对组件清单核出)", "依据", "状态"])
    for row in RISKS:
        ws.append(row)
    for r in range(2, ws.max_row + 1):
        fill_cell(ws.cell(r, 2), ws.cell(r, 2).value, RISK_FILL)
        ws.cell(r, 1).font = Font(bold=True)
    style_sheet(ws, widths=[8, 9, 30, 48, 40, 34, 16], tab_color=TAB["风险"])


# ---- Sheet 5: 站点与服务(占位示例;证据列可由 result.json 自动填充) ----
SITES = [
    # 宿主IP, 站点/服务, 端口, 类型, 管理面, HTTP状态, 返回头摘要, 备注(证据)
    ("192.0.2.1", "www.example.com", "443/80", "官网", "否", "200", "<server/安全头摘要>", "<证据>"),
    ("192.0.2.1", "<业务系统>", "0000", "业务系统", "是", "200", "<返回头摘要>", "<证据/风险编号>"),
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
