"""D-1/D-2: init --scaffold pulls up the project skeleton; report --deliver
double-writes the deliverable workbooks to <task-root>/../reports/ with
operator-provided round/title naming."""
from argparse import Namespace

from surface_intel.cli import cmd_init, cmd_report
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


def test_deliver_double_writes_with_round_and_title(tmp_path):
    task_root = tmp_path / "proj" / "task"
    _seed(task_root)
    args = Namespace(task_root=str(task_root), task_id="t", merge_tasks=None,
                     reveal_emails=False, deliver=True, round="R2", title="复查")
    assert cmd_report(args) == 0
    names = sorted(p.name for p in (tmp_path / "proj" / "reports").iterdir())
    assert any(name.startswith("R2_") and name.endswith("_复查.xlsx") for name in names)
    assert any(name.startswith("R2_") and name.endswith("_复查_analyst.xlsx") for name in names)
    # delivery is a double-write - the per-task evidence stays in place
    assert (task_root / "reports" / "t").is_dir()


def test_deliver_defaults_degrade_to_neutral_stem(tmp_path):
    task_root = tmp_path / "proj" / "task"
    _seed(task_root, task_id="task-abcdef1234567890")
    args = Namespace(task_root=str(task_root), task_id="task-abcdef1234567890",
                     merge_tasks=None, reveal_emails=False, deliver=True,
                     round=None, title=None)
    assert cmd_report(args) == 0
    names = [p.name for p in (tmp_path / "proj" / "reports").iterdir()]
    assert any(name.startswith("deliver_") and "task-abc" in name for name in names)
