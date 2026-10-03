"""R5.3: stop tokens, scope-version freezing and real scheduling modes."""
import asyncio
import json

import pytest

from surface_intel.coordinator import run_offline_task
from surface_intel.state.database import Database
from surface_intel.state.recovery import freeze_stale_actions


def _make_task(tmp_path, *, action_count=2, scope_version="scope-v1", statuses=None):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0',?)",
            (scope_version,),
        )
        connection.execute(
            """INSERT INTO scope_snapshot(scope_id,task_id,scope_version,payload_json,confirmed)
               VALUES('s1','t',?, ?, 1)""",
            (scope_version, json.dumps({"authorized_domains": ["example.com"],
                                        "confirmed": True,
                                        "scope_version": scope_version})),
        )
        for index in range(action_count):
            status = (statuses or {}).get(index, "planned")
            connection.execute(
                """INSERT INTO action(action_id,task_id,idempotency_key,adapter,status,scope_version,
                                       phase,input_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (f"action-{index}", "t", f"key-{index}", "mock_passive", status, scope_version,
                 "collecting_passive",
                 json.dumps({"target": "example.com", "capability": "passive_observation"})),
            )
    return db


def test_freeze_stale_actions_cancels_old_scope_and_keeps_completed(tmp_path):
    db = _make_task(tmp_path, action_count=3, scope_version="scope-v1", statuses={
        0: "planned", 1: "started", 2: "completed",
    })
    frozen = freeze_stale_actions(db, "t", "scope-v2")
    assert sorted(frozen) == ["action-0", "action-1"]
    rows = {row["action_id"]: dict(row) for row in db.rows("SELECT * FROM action")}
    assert rows["action-0"]["status"] == "cancelled"
    assert rows["action-0"]["error_code"] == "scope_version_frozen"
    assert rows["action-1"]["status"] == "cancelled"
    # completed old-scope results stay untouched as historical snapshots
    assert rows["action-2"]["status"] == "completed"
    assert rows["action-2"]["error_code"] is None
    events = [dict(row) for row in db.rows("SELECT * FROM event WHERE event_type='action_scope_frozen'")]
    assert len(events) == 2
    assert json.loads(events[0]["payload_json"])["new_scope_version"] == "scope-v2"


def test_serial_mode_stops_between_actions_on_pause(tmp_path):
    db = _make_task(tmp_path, action_count=2)

    from surface_intel import coordinator as coordinator_module
    from surface_intel.adapters.mock import MockPassiveAdapter

    original_execute = MockPassiveAdapter.execute

    async def pause_on_first(self, action, context):
        result = await original_execute(self, action, context)
        with db.connect() as connection:
            connection.execute("UPDATE task SET status='paused' WHERE task_id='t'")
        return result

    coordinator_module.MockPassiveAdapter.execute = pause_on_first
    try:
        result = asyncio.run(run_offline_task(db, "t", mode="serial"))
    finally:
        coordinator_module.MockPassiveAdapter.execute = original_execute

    assert result["status"] == "paused"
    statuses = {row["action_id"]: row["status"] for row in db.rows("SELECT * FROM action")}
    assert statuses["action-0"] == "completed"
    # no new work is scheduled once the stop token fired
    assert statuses["action-1"] == "planned"


def test_consecutive_error_threshold_stops_scheduling(tmp_path):
    db = _make_task(tmp_path, action_count=2)

    from surface_intel import coordinator as coordinator_module
    from surface_intel.adapters.base import AdapterResult
    from surface_intel.adapters.mock import MockPassiveAdapter

    original = MockPassiveAdapter.execute

    async def failing(self, action, context):
        return AdapterResult("failed", error="mock_failure")

    coordinator_module.MockPassiveAdapter.execute = failing
    try:
        result = asyncio.run(run_offline_task(db, "t", mode="serial", max_consecutive_errors=1))
    finally:
        coordinator_module.MockPassiveAdapter.execute = original

    assert result["status"] == "consecutive_errors"
    statuses = {row["action_id"]: row["status"] for row in db.rows("SELECT * FROM action")}
    assert statuses["action-0"] == "failed"
    assert statuses["action-1"] == "planned"  # scheduling stopped after the threshold


def test_hybrid_mode_respects_concurrency_and_completes_all(tmp_path):
    db = _make_task(tmp_path, action_count=4)
    in_flight = {"count": 0, "max": 0}

    from surface_intel import coordinator as coordinator_module
    from surface_intel.adapters.mock import MockPassiveAdapter

    original_execute = MockPassiveAdapter.execute

    async def counting_execute(self, action, context):
        in_flight["count"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["count"])
        try:
            await asyncio.sleep(0.01)
            return await original_execute(self, action, context)
        finally:
            in_flight["count"] -= 1

    coordinator_module.MockPassiveAdapter.execute = counting_execute
    try:
        result = asyncio.run(run_offline_task(db, "t", mode="hybrid", concurrency=2))
    finally:
        coordinator_module.MockPassiveAdapter.execute = original_execute

    # normal completion returns no stop status at all
    assert "status" not in result
    statuses = [row["status"] for row in db.rows("SELECT * FROM action")]
    assert all(status == "completed" for status in statuses)
    assert in_flight["max"] <= 2  # concurrency limit held


def test_delegated_mode_creates_lease_backed_subtasks_per_adapter(tmp_path):
    db = _make_task(tmp_path, action_count=2)
    result = asyncio.run(run_offline_task(db, "t", mode="delegated"))
    subtasks = [dict(row) for row in db.rows("SELECT * FROM subtask WHERE parent_task_id='t'")]
    assert len(subtasks) == 1  # all actions share the mock_passive adapter
    assert subtasks[0]["status"] == "completed"
    assert subtasks[0]["last_heartbeat_at"] is not None
    statuses = [row["status"] for row in db.rows("SELECT * FROM action")]
    assert all(status == "completed" for status in statuses)
    # idempotent: a second delegated run reuses the subtask row
    run_offline_task(db, "t", mode="delegated")
    assert len([dict(row) for row in db.rows("SELECT * FROM subtask WHERE parent_task_id='t'")]) == 1


def test_invalid_mode_rejected(tmp_path):
    db = _make_task(tmp_path, action_count=1)
    with pytest.raises(ValueError, match="invalid_mode"):
        asyncio.run(run_offline_task(db, "t", mode="quantum"))
