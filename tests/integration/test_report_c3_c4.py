"""C3 (email placeholder + local map + --reveal-emails) and C4
(budget_configured) report semantics, plus the C2 validate boolean."""
import json

from surface_intel.adapters.base import AdapterResult
from surface_intel.cli import main
from surface_intel.policy.scope import Scope
from surface_intel.redaction import email_placeholder, placeholderize
from surface_intel.state.budget import BudgetRepository
from surface_intel.state.database import Database
from surface_intel.state.observations import store_unified_observations
from surface_intel.reporting.export import build_report


def _setup(tmp_path):
    scope = Scope(authorized_domains=("example.com",), confirmed=True,
                  scope_version="scope-v1")
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','completed','1.0',?)",
            (scope.scope_version,))
        connection.execute(
            """INSERT INTO scope_snapshot(scope_id,task_id,scope_version,payload_json,confirmed)
               VALUES('s1','t','scope-v1',?,1)""",
            (json.dumps({"confirmed": True, "authorized_domains": ["example.com"],
                         "scope_version": "scope-v1"}),))
    return db, scope


def _metadata_for(payload):
    return {"source_quality": "direct", "license_status": "public_ct_log",
            "related_scope_status": "candidate"}


def test_rdap_email_is_placeholderized_and_mapped(tmp_path):
    db, scope = _setup(tmp_path)
    result = AdapterResult("completed", observations=[{
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"organizations": ["Example Org"], "abuse_contacts": "abuse@example.net"},
    }])
    stored = store_unified_observations(db, "t", scope, result,
                                        source="rdap_lookup", metadata_for=_metadata_for)
    assert stored == 1
    payload = json.loads(next(db.rows(
        "SELECT payload_json FROM observation WHERE task_id='t'"))["payload_json"])
    placeholder = email_placeholder("t", "abuse@example.net")
    assert payload["record"]["abuse_contacts"] == placeholder
    assert "abuse@example.net" not in payload_json(payload)
    mapping = next(db.rows("SELECT * FROM redaction_map WHERE task_id='t'"))
    assert mapping["placeholder"] == placeholder
    assert mapping["plaintext"] == "abuse@example.net"


def payload_json(payload):
    return json.dumps(payload)


def test_report_defaults_to_placeholders_and_reveals_on_flag(tmp_path):
    db, scope = _setup(tmp_path)
    result = AdapterResult("completed", observations=[{
        "target": "203.0.113.5", "target_kind": "ip", "status": "observed",
        "record": {"abuse_contacts": "abuse@example.net"},
    }])
    store_unified_observations(db, "t", scope, result,
                               source="rdap_lookup", metadata_for=_metadata_for)
    default_dir = tmp_path / "out-default"
    build_report(db, "t", default_dir)
    body = (default_dir / "result.json").read_text(encoding="utf-8")
    assert "abuse@example.net" not in body
    assert email_placeholder("t", "abuse@example.net") in body

    reveal_dir = tmp_path / "out-reveal"
    build_report(db, "t", reveal_dir, reveal_emails=True)
    assert "abuse@example.net" in (reveal_dir / "result.json").read_text(encoding="utf-8")


def test_upstream_placeholders_pass_through_without_mapping(tmp_path):
    # NB: built by concatenation - a literal upstream token from real task
    # data gets restored to plaintext by the environment before the test
    # process ever sees it, which would defeat the passthrough assertion.
    upstream_token = "{{" + "EMAIL_upstream7f" + "}}"
    new_payload, pairs = placeholderize(
        {"abuse_contacts": upstream_token}, "t")
    assert new_payload == {"abuse_contacts": upstream_token}
    assert pairs == []


def test_budget_configured_distinguishes_unconfigured_from_zero(tmp_path):
    db, _scope = _setup(tmp_path)
    report = build_report(db, "t", tmp_path / "out1")
    assert report["summary"]["budget_configured"] is False
    assert report["summary"]["budget_lines"] == 0

    BudgetRepository(db).create("t", "task:t", 10)
    report = build_report(db, "t", tmp_path / "out2")
    assert report["summary"]["budget_configured"] is True
    assert report["summary"]["budget_lines"] == 1


def test_validate_reports_real_boolean_and_active_fields(tmp_path, capsys):
    _setup(tmp_path)
    code = main(["validate", "t", "--task-root", str(tmp_path)])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["external_calls_allowed"] is True
    assert output["active_testing_allowed"] is False
    assert output["active_testing_level"] == "passive_only"
