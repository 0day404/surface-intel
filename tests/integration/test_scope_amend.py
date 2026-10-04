"""P1-4: controlled scope amendment for discovered assets."""
import json

import pytest

from surface_intel.cli import main
from surface_intel.state.database import Database
from surface_intel.state.recovery import freeze_stale_actions


def _seed(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','scope-v1')")
        connection.execute(
            """INSERT INTO scope_snapshot(scope_id,task_id,scope_version,payload_json,confirmed)
               VALUES('s1','t','scope-v1',?,1)""",
            (json.dumps({"authorized_domains": ["example.com"], "confirmed": True,
                         "scope_version": "scope-v1"}),),
        )
        # a candidate IP discovered by gated collection, plus one planned action
        connection.execute(
            """INSERT INTO asset(asset_id,task_id,asset_type,normalized_value,scope_status)
               VALUES('a1','t','ip','203.0.113.5','candidate')""")
        connection.execute(
            """INSERT INTO action(action_id,task_id,idempotency_key,adapter,status,scope_version,
                                   phase,input_json)
               VALUES('act-1','t','k1','mock_passive','planned','scope-v1',
                      'collecting_passive','{}')""")
    return db


def _run(tmp_path, *extra):
    capsys = {}
    code = main(["scope-amend", "--task-root", str(tmp_path), "t",
                 "--add-ips", "203.0.113.5", "--reason", "discovered via A record",
                 *extra])
    return code


def test_amend_without_confirm_prints_proposal_only(tmp_path):
    db = _seed(tmp_path)
    code = _run(tmp_path)
    assert code == 0
    # no new snapshot, task version unchanged
    assert next(db.rows("SELECT scope_version FROM task WHERE task_id='t'"))["scope_version"] == "scope-v1"
    assert next(db.rows("SELECT COUNT(*) AS count FROM scope_snapshot"))["count"] == 1


def test_amend_with_confirm_creates_version_freezes_old_actions(tmp_path):
    db = _seed(tmp_path)
    code = main(["scope-amend", "--task-root", str(tmp_path), "t",
                 "--add-ips", "203.0.113.5", "--reason", "discovered via A record",
                 "--confirm"])
    assert code == 0
    task_version = next(db.rows("SELECT scope_version FROM task WHERE task_id='t'"))["scope_version"]
    assert task_version.startswith("scope-v1-amend-")
    snapshot = next(db.rows(
        "SELECT payload_json FROM scope_snapshot WHERE scope_version=?", (task_version,)))
    payload = json.loads(snapshot["payload_json"])
    assert payload["authorized_ips"] == ["203.0.113.5"]
    assert payload["scope_version"] == task_version
    # old-version planned action frozen (R5 semantics)
    action = next(db.rows("SELECT status,error_code FROM action WHERE action_id='act-1'"))
    assert action["status"] == "cancelled"
    assert action["error_code"] == "scope_version_frozen"
    events = [dict(row) for row in db.rows("SELECT * FROM event WHERE event_type='scope_amended'")]
    assert len(events) == 1
    assert "discovered via A record" in events[0]["payload_json"]


def test_amend_refuses_values_never_discovered(tmp_path):
    db = _seed(tmp_path)
    code = main(["scope-amend", "--task-root", str(tmp_path), "t",
                 "--add-ips", "198.51.100.1", "--confirm"])
    assert code == 2
    # nothing changed
    assert next(db.rows("SELECT COUNT(*) AS count FROM scope_snapshot"))["count"] == 1


def test_amend_with_no_values_refused(tmp_path):
    code = main(["scope-amend", "--task-root", str(tmp_path), "t"])
    assert code == 2
