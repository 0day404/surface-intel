import asyncio
import json

from surface_intel.cli import main
from surface_intel.coordinator import run_offline_task
from surface_intel.reporting.export import build_report
from surface_intel.state.database import Database


def _task(tmp_path, capsys, scope):
    main(["init", str(tmp_path)])
    capsys.readouterr()
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(scope), encoding="utf-8")
    main(["create", "--task-root", str(tmp_path), "--scope", str(scope_path)])
    task_id = json.loads(capsys.readouterr().out)["task_id"]
    return Database(tmp_path / "task.sqlite"), task_id


def test_case_01_empty_passive_result_is_partial(tmp_path, capsys):
    db, task_id = _task(tmp_path, capsys, {"authorized_domains": ["example.com"], "confirmed": True})
    main(["plan", "--task-root", str(tmp_path), task_id])
    capsys.readouterr()
    asyncio.run(run_offline_task(db, task_id, [{"observations": [], "has_more": False}]))
    report = build_report(db, task_id, tmp_path / "reports")
    assert report["integrity_status"] == "partial"


def test_case_02_multipage_results_and_case_09_redaction_boundary(tmp_path, capsys):
    db, task_id = _task(tmp_path, capsys, {"authorized_domains": ["example.com"], "confirmed": True})
    main(["plan", "--task-root", str(tmp_path), task_id])
    capsys.readouterr()
    result = asyncio.run(run_offline_task(db, task_id, [
        {"observations": [{"host": "a.example.com"}], "has_more": True, "next_token": "next"},
        {"observations": [{"host": "b.example.com"}], "has_more": False},
    ]))
    assert result["assets"] == 2
    assert "Authorization" not in json.dumps(build_report(db, task_id, tmp_path / "reports"))


def test_case_06_started_action_recovery_is_unknown(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','failed','1.0','s')")
        connection.execute("INSERT INTO action(action_id,task_id,idempotency_key,adapter,status,scope_version) VALUES('a','t','k','safe_http','started','s')")
    report = build_report(db, "t", tmp_path / "reports")
    assert report["integrity_status"] == "failed"
