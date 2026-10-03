import json

from surface_intel.state.database import Database
from surface_intel.state.recovery import create_checkpoint, latest_checkpoint, recover_started_actions


def test_started_action_becomes_unknown_and_checkpoint_is_readable(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")
        connection.execute(
            "INSERT INTO action(action_id,task_id,idempotency_key,adapter,status,scope_version) VALUES('a','t','k','safe_http','started','s')"
        )
    checkpoint_id = create_checkpoint(db, "t", phase="planned", payload={"pending": ["a"]})
    assert checkpoint_id.startswith("checkpoint-")
    assert latest_checkpoint(db, "t")["payload"]["pending"] == ["a"]
    assert recover_started_actions(db, "t") == ["a"]
    assert db.health()["ok"] is True
    row = next(db.rows("SELECT status,error_code FROM action WHERE action_id='a'"))
    assert row["status"] == "unknown"
    assert row["error_code"] == "external_state_unknown"
