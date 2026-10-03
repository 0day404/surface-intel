from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from ..state.database import Database


def build_report(db: Database, task_id: str, output_dir: str | Path) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (task_id,)), None)
    if task is None:
        raise ValueError("task_not_found")
    assets = [dict(row) for row in db.rows("SELECT * FROM asset WHERE task_id=?", (task_id,))]
    observations = [dict(row) for row in db.rows("SELECT * FROM observation WHERE task_id=?", (task_id,))]
    gaps = [dict(row) for row in db.rows("SELECT * FROM gap WHERE task_id=?", (task_id,))]
    evidence = [dict(row) for row in db.rows("SELECT * FROM evidence WHERE task_id=?", (task_id,))]
    events = [dict(row) for row in db.rows("SELECT * FROM event WHERE task_id=? ORDER BY created_at", (task_id,))]
    checkpoints = [dict(row) for row in db.rows("SELECT * FROM checkpoint WHERE task_id=? ORDER BY created_at", (task_id,))]
    report = {
        "task": dict(task),
        "integrity_status": "partial" if gaps else "complete",
        "assets": assets,
        "observations": observations,
        "gaps": gaps,
        "evidence": evidence,
        "events": events,
        "checkpoints": checkpoints,
        "recovery_summary": {
            "last_checkpoint": checkpoints[-1]["checkpoint_id"] if checkpoints else None,
            "recoverable": bool(checkpoints),
            "unknown_actions": sum(
                1 for row in db.rows(
                    "SELECT status FROM action WHERE task_id=? AND status='unknown'", (task_id,)
                )
            ),
        },
        "export": {"json": str(output / "result.json"), "xlsx": str(output / "result.xlsx")},
    }
    json_path = output / "result.json"
    json_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    workbook = Workbook()
    workbook.active.title = "报告摘要"
    workbook["报告摘要"].append(["task_id", "integrity_status", "asset_count"])
    workbook["报告摘要"].append([task_id, report["integrity_status"], len(assets)])
    sheet = workbook.create_sheet("资产总表")
    if assets:
        sheet.append(list(assets[0].keys()))
        for row in assets:
            sheet.append([_excel_safe(row[key]) for key in assets[0]])
    workbook.create_sheet("信息缺口")
    for row in gaps:
        workbook["信息缺口"].append([_excel_safe(value) for value in row.values()])
    evidence_sheet = workbook.create_sheet("证据文件")
    for row in evidence:
        evidence_sheet.append([_excel_safe(value) for value in row.values()])
    log_sheet = workbook.create_sheet("收集日志")
    for row in events:
        log_sheet.append([_excel_safe(value) for value in row.values()])
    recovery_sheet = workbook.create_sheet("恢复摘要")
    recovery_sheet.append(["last_checkpoint", "recoverable", "unknown_actions"])
    recovery_sheet.append([
        report["recovery_summary"]["last_checkpoint"],
        report["recovery_summary"]["recoverable"],
        report["recovery_summary"]["unknown_actions"],
    ])
    workbook.save(output / "result.xlsx")
    return report


def _excel_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
