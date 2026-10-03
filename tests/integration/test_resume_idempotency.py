import asyncio
import json

from surface_intel.cli import main
from surface_intel.coordinator import run_offline_task
from surface_intel.state.database import Database


def test_completed_actions_and_fofa_pages_are_not_replayed(tmp_path, capsys):
    main(["init", str(tmp_path)])
    capsys.readouterr()
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"authorized_domains": ["example.com"], "confirmed": True}), encoding="utf-8")
    main(["create", "--task-root", str(tmp_path), "--scope", str(scope)])
    task_id = json.loads(capsys.readouterr().out)["task_id"]
    main(["plan", "--task-root", str(tmp_path), task_id])
    capsys.readouterr()
    db = Database(tmp_path / "task.sqlite")
    pages = [{"observations": [{"host": "edge.example.com"}], "has_more": True, "next_token": "n1"},
             {"observations": [{"host": "api.example.com"}], "has_more": False}]
    first = asyncio.run(run_offline_task(db, task_id, pages))
    second = asyncio.run(run_offline_task(db, task_id, pages))
    assert first["assets"] == 2
    assert second["assets"] == 0
    assert len(list(db.rows("SELECT * FROM action WHERE status='completed'"))) == 1
