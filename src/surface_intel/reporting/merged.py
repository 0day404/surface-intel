"""Cross-task merged report (C1).

`report --merge <task-id>...` produces one delivery workbook over several
tasks sharing a task-root. Sheets are built with direct per-task queries,
deliberately not by composing build_report outputs: that one writes per-task
side effects (result.json/xlsx) and carries single-task sections (baseline
diff, browser) that have no merged meaning. Same-valued assets from
different tasks each keep their row - scope judgments are never merged
across tasks, only presented side by side.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook

from ..state.database import Database
from .analyst_report import (
    HEADER_FONT,
    _domain_rows,
    _graph_index,
    _ip_rows,
    _rdap_countries,
    _safe,
    _service_rows,
    _sheet,
    _site_rows,
)

# code8 of the sorted task-id set: deterministic naming means re-running the
# same set overwrites in place instead of proliferating directories
MERGED_DIR_NAME = "merged"


def merged_code(task_ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(task_ids)).encode()).hexdigest()[:8]


def build_merged_report(db: Database, task_ids: list[str], merged_root: str | Path,
                        *, reveal_emails: bool = False) -> dict:
    task_ids = sorted(dict.fromkeys(task_ids))
    missing = [tid for tid in task_ids
               if next(db.rows("SELECT 1 FROM task WHERE task_id=?", (tid,)), None) is None]
    if missing:
        raise ValueError(f"task_not_found:{','.join(missing)}")

    code = merged_code(task_ids)
    output = Path(merged_root) / code
    output.mkdir(parents=True, exist_ok=True)

    reveal_pairs: list[tuple[str, str]] = []
    if reveal_emails:
        # C3: union of the per-task maps - same placeholder always maps to the
        # same plaintext within a task, and placeholders are task-scoped
        reveal_pairs = [(row["placeholder"], row["plaintext"]) for row in db.rows(
            f"SELECT placeholder,plaintext FROM redaction_map "
            f"WHERE task_id IN ({','.join('?' * len(task_ids))})", task_ids)]

    def reveal(node):
        if isinstance(reveal_pairs, list) and reveal_pairs:
            if isinstance(node, dict):
                return {key: reveal(value) for key, value in node.items()}
            if isinstance(node, list):
                return [reveal(item) for item in node]
            if isinstance(node, str):
                for placeholder, plaintext in reveal_pairs:
                    if placeholder in node:
                        node = node.replace(placeholder, plaintext)
        return node

    tasks = [dict(row) for row in db.rows(
        f"SELECT task_id,status,scope_version FROM task WHERE task_id IN ({','.join('?' * len(task_ids))}) "
        "ORDER BY task_id", task_ids)]

    summary_rows: list[list] = []
    domain_rows: list[list] = []
    ip_rows: list[list] = []
    site_rows: list[list] = []
    service_rows: list[list] = []
    total_conflicts = 0
    for task in tasks:
        tid = task["task_id"]
        t_assets = [dict(row) for row in db.rows("SELECT * FROM asset WHERE task_id=?", (tid,))]
        t_relations = [dict(row) for row in db.rows(
            "SELECT * FROM asset_relation WHERE task_id=? ORDER BY rowid", (tid,))]
        t_observations = [dict(row) for row in db.rows(
            "SELECT * FROM observation WHERE task_id=?", (tid,))]
        conflicts = next(db.rows(
            "SELECT COUNT(*) AS count FROM asset_conflict WHERE task_id=?", (tid,)))["count"]
        total_conflicts += conflicts
        summary_rows.append([tid, task["status"], task["scope_version"],
                             len(t_assets), len(t_observations), conflicts])
        index = _graph_index(t_assets, t_relations)
        country = _rdap_countries(t_observations)
        domain_rows.extend([tid, *row] for row in _domain_rows(index))
        ip_rows.extend([tid, *row] for row in _ip_rows(index, country))
        site_rows.extend([tid, *row] for row in _site_rows(t_observations))
        service_rows.extend([tid, *row] for row in _service_rows(index))
    summary_rows.append(["合计", "", "", sum(row[3] for row in summary_rows),
                         sum(row[4] for row in summary_rows), total_conflicts])

    workbook = Workbook()
    summary = workbook.active
    summary.title = "报告摘要"
    summary.append(["task_id", "status", "scope_version", "asset_count",
                    "observation_count", "unresolved_conflicts"])
    for cell in summary[1]:
        cell.font = HEADER_FONT
    for row in summary_rows:
        summary.append([_safe(value) for value in row])
    _sheet(workbook, "域名资产",
           ["task_id", "域名资产", "归属档", "用途", "已发现子域", "归属依据", "数据来源"],
           [reveal(row) for row in domain_rows])
    _sheet(workbook, "IP资产",
           ["task_id", "IP", "归属档", "位置/机房", "承载资产", "收录端口", "备注"],
           [reveal(row) for row in ip_rows])
    _sheet(workbook, "站点", ["task_id", "站点", "标题/状态", "说明"],
           [reveal(row) for row in site_rows])
    _sheet(workbook, "服务", ["task_id", "IP", "端口", "服务", "归属档", "说明"],
           [reveal(row) for row in service_rows])

    manifest = {
        "task_ids": task_ids,
        "code": code,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")

    xlsx_path = output / "merged_report.xlsx"
    # atomic publish, matching the per-task workbook discipline
    handle = tempfile.NamedTemporaryFile(prefix="merged-", suffix=".xlsx",
                                         dir=str(output), delete=False)
    handle.close()
    temp_path = Path(handle.name)
    try:
        workbook.save(temp_path)
        os.replace(temp_path, xlsx_path)
        return {"code": code, "task_ids": task_ids, "path": str(xlsx_path),
                "manifest": str(output / "manifest.json"), "xlsx_status": "exported"}
    except Exception as exc:
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            return {"code": code, "task_ids": task_ids, "path": str(xlsx_path),
                    "xlsx_status": "failed", "xlsx_error": f"{type(exc).__name__}: {exc}"}
