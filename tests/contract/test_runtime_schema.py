from surface_intel.state.database import Database, SCHEMA_VERSION


def test_runtime_schema_has_action_call_audit_and_extended_action_fields(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    assert db.health()["schema_version"] == SCHEMA_VERSION == 4
    action_columns = {row[1] for row in db.connect().execute("PRAGMA table_info(action)").fetchall()}
    assert {"phase", "target_ref", "input_json", "cost_units", "request_id", "next_cursor_ref",
            "external_state", "last_error_code"} <= action_columns
    assert next(db.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='adapter_call'"), None)
