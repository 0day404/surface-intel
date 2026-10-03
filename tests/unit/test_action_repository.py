from surface_intel.state.actions import ActionRepository
from surface_intel.state.database import Database


def test_action_repository_claim_is_idempotent_and_terminal_state_is_persisted(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")
    repository = ActionRepository(db)
    action_id = repository.create("t", "mock_passive", "s", "t:key", {"target": "example.com"})

    assert repository.claim(action_id) is True
    assert repository.claim(action_id) is False
    assert repository.complete(action_id, "result-1", request_id="req-1", cost_units=1) is True
    assert repository.complete(action_id, "result-2") is False
    row = next(db.rows("SELECT status,request_id,cost_units,external_state FROM action WHERE action_id=?", (action_id,)))
    assert row["status"] == "completed"
    assert row["request_id"] == "req-1"
    assert row["cost_units"] == 1
    assert row["external_state"] == "completed"
