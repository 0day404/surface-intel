import json

from surface_intel.cli import main
from surface_intel.state.database import Database


def test_confirmed_scope_preflight_and_idempotent_plan(tmp_path, capsys):
    main(["init", str(tmp_path)])
    capsys.readouterr()
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({
        "scope_version": "scope-v0001",
        "authorized_domains": ["Example.com."],
        "confirmed": True,
    }), encoding="utf-8")
    main(["create", "--task-root", str(tmp_path), "--scope", str(scope)])
    task_id = json.loads(capsys.readouterr().out)["task_id"]
    assert main(["preflight", "--task-root", str(tmp_path), task_id]) == 0
    assert main(["plan", "--task-root", str(tmp_path), task_id]) == 0
    capsys.readouterr()
    assert main(["plan", "--task-root", str(tmp_path), task_id]) == 0
    rows = list(Database(tmp_path / "task.sqlite").rows("SELECT * FROM action"))
    assert len(rows) == 1
