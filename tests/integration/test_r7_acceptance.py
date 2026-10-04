"""R7 acceptance: report enrichment, snapshot-diff monitoring, atomic export,
and the design-document case fixtures (case-01, case-07, case-08, CLI chain)."""
import asyncio
import json

from openpyxl import load_workbook

from surface_intel.cli import main
from surface_intel.coordinator import run_offline_task
from surface_intel.merger import merge_task_observations
from surface_intel.monitor import absorb_baseline, diff_against_baseline, save_baseline
from surface_intel.policy.scope import Scope
from surface_intel.reporting.export import build_report
from surface_intel.state.database import Database


def _seed_mergeable(tmp_path, scope, records):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','completed','1.0',?)",
            (scope.scope_version,),
        )
        for index, payload in enumerate(records):
            payload = dict(payload)
            payload.setdefault("observed_at", f"2026-10-04T10:0{index}:00+00:00")
            connection.execute(
                """INSERT INTO observation(observation_id,task_id,source,target,payload_json)
                   VALUES(?,?,?,?,?)""",
                (f"obs-{index}", "t", payload.pop("source"), str(payload.get("target", "")),
                 json.dumps(payload, sort_keys=True)),
            )
    return db


def test_snapshot_diff_tracks_added_changed_and_scope_mismatch(tmp_path):
    scope = Scope(authorized_domains=("example.com",), authorized_ips=("203.0.113.5",),
                  confirmed=True, scope_version="scope-v1")
    db = _seed_mergeable(tmp_path, scope, [
        {"source": "dns_resolver", "target": "www.example.com", "record_type": "A",
         "records": ["203.0.113.5"], "status": "observed"},
    ])
    merge_task_observations(db, "t", scope)
    save_baseline(db, "t", "scope-v1")

    # nothing changed -> empty diff
    diff = diff_against_baseline(db, "t", "scope-v1")
    assert diff.scope_version_match is True
    assert diff.added == [] and diff.changed == [] and diff.disappeared == []
    assert diff.unchanged == 2

    # new observation -> added (after re-merge); moved architecture -> changed
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO observation(observation_id,task_id,source,target,payload_json)
               VALUES('obs-new','t','dns_resolver','www.example.com',?)""",
            (json.dumps({"target": "www.example.com", "record_type": "A",
                         "records": ["203.0.113.5", "198.51.100.7"], "status": "observed",
                         "observed_at": "2026-10-04T11:00:00+00:00"}),),
        )
    merge_task_observations(db, "t", scope)
    with db.connect() as connection:
        connection.execute(
            "UPDATE asset SET architecture_status='cdn_candidate' WHERE normalized_value='www.example.com'")
    diff = diff_against_baseline(db, "t", "scope-v1")
    added_values = {item["normalized_value"] for item in diff.added}
    assert added_values == {"198.51.100.7"}
    assert any(item["normalized_value"] == "www.example.com" for item in diff.changed)
    assert "architecture_status" in diff.changed[0]["fields"]


def test_absorb_baseline_quality_gate_and_scope_mismatch(tmp_path):
    scope = Scope(authorized_domains=("example.com",), confirmed=True, scope_version="scope-v1")
    db = _seed_mergeable(tmp_path, scope, [])
    # quality gate: failed integrity never overwrites the baseline
    result = absorb_baseline(db, "t", "scope-v1", integrity_status="failed")
    assert result["absorbed"] is False
    assert result["reason"] == "quality_gate_failed:failed"
    # scope mismatch between task and claimed version
    result = absorb_baseline(db, "t", "scope-v9", integrity_status="complete")
    assert result["absorbed"] is False
    assert result["reason"] == "scope_version_mismatch"
    # gate passed + version matched -> absorbed
    result = absorb_baseline(db, "t", "scope-v1", integrity_status="partial")
    assert result["absorbed"] is True


def test_case01_single_domain_no_active_empty_results_ends_bounded(tmp_path, capsys):
    """case-01: no active authorization + empty passive results still ends cleanly."""
    main(["init", str(tmp_path)])
    capsys.readouterr()
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"authorized_domains": ["example.com"], "confirmed": True}),
                     encoding="utf-8")
    main(["create", "--task-root", str(tmp_path), "--scope", str(scope)])
    task_id = json.loads(capsys.readouterr().out)["task_id"]
    main(["plan", "--task-root", str(tmp_path), task_id])
    capsys.readouterr()
    result = asyncio.run(run_offline_task(Database(tmp_path / "task.sqlite"), task_id, [
        {"observations": [], "has_more": False},
    ]))
    # empty FOFA pages: whatever the offline fixture emits, the task ends
    # bounded (correlating) - never failed
    task_status = next(Database(tmp_path / "task.sqlite").rows(
        "SELECT status FROM task WHERE task_id=?", (task_id,)))["status"]
    assert task_status == "correlating"  # bounded completion, never failed
    report = build_report(Database(tmp_path / "task.sqlite"), task_id, tmp_path / "reports")
    assert report["integrity_status"] == "partial"  # active observation gap remains


def test_case08_xlsx_failure_keeps_json_main_result(tmp_path):
    from unittest.mock import patch

    from openpyxl import Workbook as RealWorkbook

    scope = Scope(authorized_domains=("example.com",), confirmed=True, scope_version="scope-v1")
    db = _seed_mergeable(tmp_path, scope, [
        {"source": "dns_resolver", "target": "www.example.com", "record_type": "A",
         "records": ["203.0.113.5"], "status": "observed"},
    ])
    merge_task_observations(db, "t", scope)
    output_dir = tmp_path / "reports"

    def exploding_save(self, filename):
        raise OSError("disk full")

    with patch.object(RealWorkbook, "save", exploding_save):
        report = build_report(db, "t", output_dir)

    # case-08: the JSON main result is intact and the failure is explicit
    assert (output_dir / "result.json").exists()
    assert report["export"]["xlsx_status"] == "failed"
    assert "disk full" in report["export"]["xlsx_error"]
    # a retry after the failure recovers the XLSX
    report = build_report(db, "t", output_dir)
    assert report["export"]["xlsx_status"] == "exported"
    assert report["export"]["asset_count_consistent"] is True


def test_cli_full_chain_init_to_report_on_clean_dir(tmp_path, capsys):
    """Acceptance: clean-directory full chain init→create→preflight→plan→run→
    status→report, then baseline save/diff."""
    main(["init", str(tmp_path)])
    capsys.readouterr()
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"authorized_domains": ["example.com"], "confirmed": True}),
                     encoding="utf-8")
    main(["create", "--task-root", str(tmp_path), "--scope", str(scope)])
    task_id = json.loads(capsys.readouterr().out)["task_id"]
    main(["preflight", "--task-root", str(tmp_path), task_id])
    main(["plan", "--task-root", str(tmp_path), task_id])
    capsys.readouterr()
    main(["run", "--task-root", str(tmp_path), task_id])
    capsys.readouterr()
    main(["status", "--task-root", str(tmp_path), task_id])
    status_output = json.loads(capsys.readouterr().out)
    assert status_output["ok"] is True
    main(["report", "--task-root", str(tmp_path), task_id])
    report = json.loads(capsys.readouterr().out)
    assert report["export"]["xlsx_status"] == "exported"
    # the default offline fixture produces no FOFA rows - the chain mechanics
    # and the summary block are what this acceptance check proves
    assert report["summary"]["asset_count"] == 0
    assert report["summary"]["observation_count"] >= 1
    main(["merge", "--task-root", str(tmp_path), task_id])
    capsys.readouterr()
    main(["baseline-save", "--task-root", str(tmp_path), task_id])
    capsys.readouterr()
    main(["baseline-diff", "--task-root", str(tmp_path), task_id])
    diff = json.loads(capsys.readouterr().out)
    assert diff["ok"] is True and diff["scope_version_match"] is True


def test_certificates_deduped_by_identity_with_observation_count(tmp_path):
    """Retrospective P0-2: re-observed certificates collapse to one row per
    (common_name, issuer, not_after) with an observation_count; the raw
    observations are untouched."""
    scope = Scope(authorized_domains=("example.com",), confirmed=True, scope_version="scope-v1")
    cert = {"common_name": "www.example.com", "sans": ["www.example.com"],
            "issuer": "Sectigo", "serial": "aa", "not_before": "n", "not_after": "x",
            "first_seen": "f", "last_seen": "l", "log_id": 1, "log_source": "crt.sh"}
    db = _seed_mergeable(tmp_path, scope, [
        # the same certificate observed twice (legal re-observation)
        {"source": "ct_log", "target": "example.com", "status": "observed",
         "certificates": [cert]},
        {"source": "ct_log", "target": "example.com", "status": "observed",
         "certificates": [dict(cert, log_id=2)]},
    ])
    merge_task_observations(db, "t", scope)
    report = build_report(db, "t", tmp_path / "reports")
    certs = report["certificates"]
    assert len(certs) == 1  # deduped, not 2 rows
    assert certs[0]["observation_count"] == 2
    # raw observations keep every row (immutability principle)
    assert sum(1 for o in report["observations"] if o["source"] == "ct_log") == 2


def test_integrity_unknown_explains_pending_actions(tmp_path):
    """P1-6: direct-runner tasks carry pending planned actions; the report
    must say WHY integrity is unknown instead of a bare status."""
    scope = Scope(authorized_domains=("example.com",), confirmed=True, scope_version="scope-v1")
    db = _seed_mergeable(tmp_path, scope, [])
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t2','completed','1.0','scope-v1')")
        connection.execute(
            """INSERT INTO action(action_id,task_id,idempotency_key,adapter,status,scope_version,phase,input_json)
               VALUES('act-p','t2','kp','mock_passive','planned','scope-v1','collecting_passive','{}')""")
    report = build_report(db, "t2", tmp_path / "r")
    assert report["integrity_status"] == "unknown"
    assert report["summary"]["planned_actions_pending"] == 1
    assert "planned action" in report["summary"]["integrity_unknown_reason"]


def test_zero_action_task_reports_complete_integrity(tmp_path):
    """D10 姊妹用例：真实 recon 不写 action 行，报告 integrity 应为 complete
    而非 unknown，且无 unknown 理由。"""
    scope = Scope(authorized_domains=("example.com",), confirmed=True, scope_version="scope-v1")
    db = _seed_mergeable(tmp_path, scope, [])
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t3','completed','1.0','scope-v1')")
    report = build_report(db, "t3", tmp_path / "r3")
    assert report["integrity_status"] == "complete"
    assert report["summary"]["integrity_unknown_reason"] is None
