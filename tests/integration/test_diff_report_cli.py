"""B7: diff-report CLI——渲染落盘到 reports/<task-id>/、checkpoint 缺失诚实报错。"""
import json

import surface_intel.cli as cli
import surface_intel.monitor
from surface_intel.monitor import SnapshotDiff
from surface_intel.state.database import Database


def _seed(tmp_path, scope_version="scope-v1"):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) "
            "VALUES('t','planned','1.0',?)", (scope_version,))
    return db


def test_diff_report_writes_markdown_to_reports_dir(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    diff = SnapshotDiff(
        scope_version="scope-v1", baseline_scope_version="scope-v1",
        scope_version_match=True,
        added=[{"asset_type": "domain", "normalized_value": "x.example.com"}],
        disappeared=[], changed=[], reappeared=[], unchanged=3)

    monkeypatch.setattr(surface_intel.monitor, "diff_against_baseline",
                        lambda db, task_id, scope_version: diff)
    code = cli.main(["diff-report", "t", "--task-root", str(tmp_path)])
    output = json.loads(capsys.readouterr().out)
    assert code == 0
    assert output["ok"] is True and output["added"] == 1 and output["unchanged"] == 3
    report = open(output["output"], encoding="utf-8").read()
    assert "# 差量复查报告 — t" in report
    assert "x.example.com" in report
    # 缺省落 reports/<task-id>/
    assert f"reports{chr(92)}t{chr(92)}diff-report-" in output["output"] or \
        f"reports/t/diff-report-" in output["output"]


def test_diff_report_checkpoint_pair_and_missing_checkpoint(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    calls = []

    def fake_diff_checkpoints(db, task_id, baseline, against=None):
        calls.append((baseline, against))
        raise ValueError("checkpoint_not_found")

    monkeypatch.setattr(surface_intel.monitor, "diff_checkpoints", fake_diff_checkpoints)
    code = cli.main(["diff-report", "t", "--task-root", str(tmp_path),
                     "--baseline", "cp-1", "--against", "cp-2",
                     "--output", str(tmp_path / "out.md")])
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["error"] == "checkpoint_not_found"
    assert calls == [("cp-1", "cp-2")]


def test_diff_report_task_not_found(tmp_path, capsys):
    code = cli.main(["diff-report", "missing", "--task-root", str(tmp_path)])
    output = json.loads(capsys.readouterr().out)
    assert code == 2
    assert output["error"] == "task_not_found"
