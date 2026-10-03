from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from ..state.database import Database


def compute_integrity_status(
    task_status: str,
    *,
    gaps: list[dict],
    unknown_actions: int,
    failed_actions: int,
    pending_actions: int,
) -> str:
    if task_status in {"failed"}:
        return "failed"
    if task_status in {"paused", "blocked", "cancelled", "needs_review"}:
        return "partial"
    if failed_actions and not gaps:
        return "partial"
    if unknown_actions or pending_actions:
        return "unknown" if not gaps else "partial"
    if gaps:
        return "partial"
    return "complete"


def build_report(db: Database, task_id: str, output_dir: str | Path) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    task = next(db.rows("SELECT * FROM task WHERE task_id=?", (task_id,)), None)
    if task is None:
        raise ValueError("task_not_found")
    assets = [dict(row) for row in db.rows("SELECT * FROM asset WHERE task_id=?", (task_id,))]
    asset_relations = [dict(row) for row in db.rows(
        "SELECT * FROM asset_relation WHERE task_id=? ORDER BY rowid", (task_id,),
    )]
    asset_conflicts = [dict(row) for row in db.rows(
        "SELECT * FROM asset_conflict WHERE task_id=? ORDER BY rowid", (task_id,),
    )]
    observations = [dict(row) for row in db.rows("SELECT * FROM observation WHERE task_id=?", (task_id,))]
    gaps = [dict(row) for row in db.rows("SELECT * FROM gap WHERE task_id=?", (task_id,))]
    evidence = [dict(row) for row in db.rows("SELECT * FROM evidence WHERE task_id=?", (task_id,))]
    events = [dict(row) for row in db.rows("SELECT * FROM event WHERE task_id=? ORDER BY created_at", (task_id,))]
    checkpoints = [dict(row) for row in db.rows("SELECT * FROM checkpoint WHERE task_id=? ORDER BY created_at", (task_id,))]
    fofa_batches = [dict(row) for row in db.rows(
        "SELECT * FROM fofa_query_batch WHERE task_id=? ORDER BY batch_index, batch_id", (task_id,)
    )]
    fofa_plans = [dict(row) for row in db.rows(
        "SELECT * FROM fofa_query_plan WHERE task_id=? ORDER BY created_at", (task_id,)
    )]
    unknown_actions = sum(1 for row in db.rows(
        "SELECT status FROM action WHERE task_id=? AND status='unknown'", (task_id,)
    ))
    failed_actions = sum(1 for row in db.rows(
        "SELECT status FROM action WHERE task_id=? AND status='failed'", (task_id,)
    ))
    pending_actions = sum(1 for row in db.rows(
        "SELECT status FROM action WHERE task_id=? AND status IN ('planned','started')", (task_id,)
    ))
    integrity_status = compute_integrity_status(
        task["status"],
        gaps=gaps,
        unknown_actions=unknown_actions,
        failed_actions=failed_actions,
        pending_actions=pending_actions,
    )
    report = {
        "task": dict(task),
        "integrity_status": integrity_status,
        "assets": assets,
        "asset_relations": asset_relations,
        "asset_conflicts": asset_conflicts,
        "merge_summary": {
            "asset_count": len(assets),
            "confirmed_assets": sum(row["scope_status"] == "confirmed" for row in assets),
            "candidate_assets": sum(row["scope_status"] == "candidate" for row in assets),
            "third_party_assets": sum(row["architecture_status"] == "third_party" for row in assets),
            "relation_count": len(asset_relations),
            "confirmed_relations": sum(row["status"] == "confirmed" for row in asset_relations),
            "unresolved_conflicts": sum(row["status"] == "unresolved" for row in asset_conflicts),
            "sources": sorted({item["source"] for row in assets
                               for item in json.loads(row.get("sources_json") or "[]")}),
        },
        "observations": observations,
        "gaps": gaps,
        "evidence": evidence,
        "events": events,
        "checkpoints": checkpoints,
        "fofa": {
            "plans": fofa_plans,
            "batches": fofa_batches,
            "coverage": {
                "planned": len(fofa_plans),
                "batches": len(fofa_batches),
                "completed_batches": sum(row["status"] == "completed" for row in fofa_batches),
                "failed_batches": sum(row["status"] == "failed" for row in fofa_batches),
                "observations": sum(row["result_count"] for row in fofa_batches),
                "estimated_fpoints": sum(row["estimated_fpoints"] for row in fofa_batches),
                "consumed_fpoints": sum(row["consumed_fpoints"] for row in fofa_batches),
            },
        },
        "recovery_summary": {
            "last_checkpoint": checkpoints[-1]["checkpoint_id"] if checkpoints else None,
            "recoverable": bool(checkpoints),
            "unknown_actions": unknown_actions,
            "failed_actions": failed_actions,
            "pending_actions": pending_actions,
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
    relations_sheet = workbook.create_sheet("资产关系")
    if asset_relations:
        relations_sheet.append(list(asset_relations[0].keys()))
        for row in asset_relations:
            relations_sheet.append([_excel_safe(row[key]) for key in asset_relations[0]])
    conflicts_sheet = workbook.create_sheet("归并冲突")
    conflicts_sheet.append(["conflict_id", "asset_id", "attribute", "values_json", "status", "created_at"])
    for row in asset_conflicts:
        conflicts_sheet.append([
            _excel_safe(row["conflict_id"]), _excel_safe(row["asset_id"]),
            _excel_safe(row["attribute"]), _excel_safe(row["values_json"]),
            _excel_safe(row["status"]), _excel_safe(row["created_at"]),
        ])
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
    recovery_sheet.append(["last_checkpoint", "recoverable", "unknown_actions", "failed_actions", "pending_actions"])
    recovery_sheet.append([
        report["recovery_summary"]["last_checkpoint"],
        report["recovery_summary"]["recoverable"],
        report["recovery_summary"]["unknown_actions"],
        report["recovery_summary"]["failed_actions"],
        report["recovery_summary"]["pending_actions"],
    ])
    fofa_sheet = workbook.create_sheet("FOFA查询")
    fofa_sheet.append(["query_plan_count", "batch_count", "completed_batches", "failed_batches",
                       "observations", "estimated_fpoints", "consumed_fpoints"])
    coverage = report["fofa"]["coverage"]
    fofa_sheet.append([coverage["planned"], coverage["batches"], coverage["completed_batches"],
                       coverage["failed_batches"], coverage["observations"],
                       coverage["estimated_fpoints"], coverage["consumed_fpoints"]])
    workbook.save(output / "result.xlsx")
    return report


def _excel_safe(value):
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
