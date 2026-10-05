"""D-1: init --scaffold pulls up the project skeleton. Report export modes
(2026-10-05 operator decision): the default `report` is JSON-ONLY -
result.json is the machine-readable intermediate and the delivered workbook
is built from it by scripts/build_deliverable_xlsx.py. The xlsx renderings
(result.xlsx / analyst_report.xlsx) survive as an opt-in via `--xlsx` /
include_xlsx for humans inspecting a draft."""
import json
from argparse import Namespace

from surface_intel.cli import cmd_init, main
from surface_intel.reporting.export import build_report
from surface_intel.state.database import Database


def _seed(task_root, task_id="t"):
    task_root.mkdir(parents=True)
    db = Database(task_root / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES(?, 'planned','1.0','s')",
            (task_id,))


def test_init_scaffold_pulls_up_project_skeleton(tmp_path):
    task_root = tmp_path / "proj" / "task"
    args = Namespace(task_root=str(task_root), scaffold=True)
    assert cmd_init(args) == 0
    project = tmp_path / "proj"
    for name in ("inputs", "reports", "data"):
        assert (project / name).is_dir()
    notes = project / "NOTES.md"
    assert notes.exists()
    assert "判定口径" in notes.read_text(encoding="utf-8")
    assert cmd_init(args) == 0  # idempotent


def test_report_defaults_to_json_only(tmp_path):
    task_root = tmp_path / "proj" / "task"
    _seed(task_root)
    out = tmp_path / "reports"
    report = build_report(Database(task_root / "task.sqlite"), "t", out)
    assert (out / "result.json").exists()
    # no xlsx intermediates by default - the export block stays empty
    assert not (out / "result.xlsx").exists()
    assert not (out / "analyst_report.xlsx").exists()
    assert report["export"] == {}


def test_report_xlsx_opt_in_renders_both_workbooks(tmp_path):
    task_root = tmp_path / "proj" / "task"
    _seed(task_root)
    out = tmp_path / "reports"
    report = build_report(Database(task_root / "task.sqlite"), "t", out,
                          include_xlsx=True)
    assert (out / "result.json").exists()
    assert (out / "result.xlsx").exists()
    assert (out / "analyst_report.xlsx").exists()
    assert report["export"]["xlsx_status"] == "exported"
    assert report["export"]["analyst_report"]["status"] == "exported"


def test_cli_report_defaults_to_json_only(tmp_path, capsys):
    task_root = tmp_path / "proj" / "task"
    _seed(task_root)
    main(["report", "--task-root", str(task_root), "t"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["export"] == {}
    reports_dir = task_root.parent / "task" / "reports" / "t"
    assert (reports_dir / "result.json").exists()
    assert not (reports_dir / "result.xlsx").exists()
