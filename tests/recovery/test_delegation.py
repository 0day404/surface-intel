from surface_intel.state.database import Database
from surface_intel.state.delegation import acquire_lease, create_subtask, expire_leases, heartbeat, submit_result


def test_lease_heartbeat_expiry_and_duplicate_result(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")
    subtask_id = create_subtask(
        db, parent_task_id="t", role="browser_observer", phase="observing_browser",
        scope_version="s", allowed_tools=["playwright_public_observation"],
        forbidden_actions=["state_change"], idempotency_key="subtask-key",
    )
    lease = acquire_lease(db, subtask_id, "worker-1", ttl_seconds=60)
    assert lease
    assert heartbeat(db, lease)
    result = submit_result(db, subtask_id=subtask_id, idempotency_key="result-key", status="partial", payload={"observations": []})
    assert result["accepted"]
    duplicate = submit_result(db, subtask_id=subtask_id, idempotency_key="result-key", status="partial", payload={"observations": ["ignored"]})
    assert duplicate["duplicate"]


def test_expired_lease_marks_subtask_timed_out(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")
    subtask_id = create_subtask(
        db, parent_task_id="t", role="collector", phase="collecting_passive",
        scope_version="s", allowed_tools=["mock"], forbidden_actions=[], idempotency_key="subtask-key",
    )
    lease = acquire_lease(db, subtask_id, "worker-1", ttl_seconds=-1)
    assert lease
    assert expire_leases(db) == [subtask_id]
    row = next(db.rows("SELECT status,error_code FROM subtask WHERE subtask_id=?", (subtask_id,)))
    assert row["status"] == "timed_out"
    assert row["error_code"] == "lease_expired"
