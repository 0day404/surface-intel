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


def test_allow_active_alone_upgrades_to_safe_discovery(tmp_path, capsys):
    """A1: the missing piece - a passive task can become an active-authorized
    one in place, no new task required."""
    db = _seed(tmp_path)
    code = main(["scope-amend", "--task-root", str(tmp_path), "t",
                 "--allow-active", "--confirm", "--reason", "upgrade to active observation"])
    assert code == 0
    task_version = next(db.rows("SELECT scope_version FROM task WHERE task_id='t'"))["scope_version"]
    payload = json.loads(next(db.rows(
        "SELECT payload_json FROM scope_snapshot WHERE scope_version=?", (task_version,)))["payload_json"])
    assert payload["active_testing_allowed"] is True
    assert payload["active_testing_level"] == "safe_discovery"
    event = next(db.rows("SELECT payload_json FROM event WHERE event_type='scope_amended'"))
    event_payload = json.loads(event["payload_json"])
    assert event_payload["active_testing_allowed"] is True
    assert event_payload["source"] == "manual"


def test_active_level_and_ports_ride_allow_active(tmp_path):
    db = _seed(tmp_path)
    code = main(["scope-amend", "--task-root", str(tmp_path), "t",
                 "--add-ips", "203.0.113.5", "--allow-active",
                 "--active-level", "safe_probe", "--ports", "80,443", "--confirm"])
    assert code == 0
    task_version = next(db.rows("SELECT scope_version FROM task WHERE task_id='t'"))["scope_version"]
    payload = json.loads(next(db.rows(
        "SELECT payload_json FROM scope_snapshot WHERE scope_version=?", (task_version,)))["payload_json"])
    assert payload["active_testing_level"] == "safe_probe"
    assert payload["allowed_ports"] == [80, 443]
    event = json.loads(next(db.rows(
        "SELECT payload_json FROM event WHERE event_type='scope_amended'"))["payload_json"])
    assert event["added_ports"] == [80, 443]


def test_active_level_without_allow_active_refused(tmp_path, capsys):
    _seed(tmp_path)
    code = main(["scope-amend", "--task-root", str(tmp_path), "t",
                 "--active-level", "safe_probe", "--confirm"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "allow_active_required"


def test_ports_without_allow_active_refused(tmp_path, capsys):
    _seed(tmp_path)
    code = main(["scope-amend", "--task-root", str(tmp_path), "t",
                 "--ports", "443", "--confirm"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "ports_require_allow_active"


def test_out_of_range_port_refused(tmp_path, capsys):
    _seed(tmp_path)
    code = main(["scope-amend", "--task-root", str(tmp_path), "t",
                 "--allow-active", "--ports", "70000", "--confirm"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_ports"


def test_intrusive_test_is_unreachable(tmp_path):
    """argparse choices physically exclude the intrusive level - the
    fail-closed guarantee does not depend on runtime checks."""
    _seed(tmp_path)
    with pytest.raises(SystemExit):
        main(["scope-amend", "--task-root", str(tmp_path), "t",
              "--allow-active", "--active-level", "intrusive_test", "--confirm"])


def test_confirm_new_domain_channel_audits_user_confirmation(tmp_path):
    """A2: explicitly user-confirmed never-seen domains amend, with the
    confirmation source recorded in the audit event."""
    db = _seed(tmp_path)
    code = main(["scope-amend", "--task-root", str(tmp_path), "t",
                 "--add-domains", "partner.example.org",
                 "--confirm-new-domain", "--confirm", "--reason", "user expanded scope"])
    assert code == 0
    task_version = next(db.rows("SELECT scope_version FROM task WHERE task_id='t'"))["scope_version"]
    payload = json.loads(next(db.rows(
        "SELECT payload_json FROM scope_snapshot WHERE scope_version=?", (task_version,)))["payload_json"])
    assert "partner.example.org" in payload["authorized_domains"]
    event = json.loads(next(db.rows(
        "SELECT payload_json FROM event WHERE event_type='scope_amended'"))["payload_json"])
    assert event["user_confirmed_new"] is True
    assert event["user_confirmed_new_domains"] == ["partner.example.org"]


def test_new_domain_without_channel_still_refused(tmp_path, capsys):
    _seed(tmp_path)
    code = main(["scope-amend", "--task-root", str(tmp_path), "t",
                 "--add-domains", "partner.example.org", "--confirm"])
    assert code == 2
    body = json.loads(capsys.readouterr().out)
    assert body["error"] == "values_not_discovered_by_this_task"
    assert body["detail"] == ["partner.example.org"]
