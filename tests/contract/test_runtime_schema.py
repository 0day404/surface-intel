from surface_intel.state.database import Database, SCHEMA_VERSION


def test_runtime_schema_has_action_call_audit_and_extended_action_fields(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    assert db.health()["schema_version"] == SCHEMA_VERSION == 11
    action_columns = {row[1] for row in db.connect().execute("PRAGMA table_info(action)").fetchall()}
    assert {"phase", "target_ref", "input_json", "cost_units", "request_id", "next_cursor_ref",
            "external_state", "last_error_code"} <= action_columns
    assert next(db.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='adapter_call'"), None)
    assert next(db.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='budget'"), None)
    assert next(db.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='fofa_query_batch'"), None)
    assert next(db.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='fofa_cursor'"), None)
    assert next(db.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='fofa_query_plan'"), None)


def test_migration_8_extends_existing_asset_table_and_adds_merge_tables(tmp_path):
    """A schema-7 database upgrades in place: asset gains merge columns, the
    relation/conflict tables are created, no data is lost."""
    db = Database(tmp_path / "task.sqlite")
    with db.connect() as connection:
        # simulate the pre-migration-8 shape minimally
        connection.executescript(
            """
            CREATE TABLE schema_version (version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE task (task_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                schema_version TEXT NOT NULL, scope_version TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE asset (asset_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES task(task_id),
                asset_type TEXT NOT NULL, normalized_value TEXT NOT NULL,
                scope_status TEXT NOT NULL,
                architecture_status TEXT NOT NULL DEFAULT 'unknown',
                confidence REAL NOT NULL DEFAULT 0,
                UNIQUE(task_id, asset_type, normalized_value));
            INSERT INTO task(task_id,status,schema_version,scope_version)
                VALUES('t','planned','1.0','scope-v1');
            INSERT INTO asset(asset_id,task_id,asset_type,normalized_value,scope_status)
                VALUES('asset-old','t','domain','example.com','confirmed');
            INSERT INTO schema_version(version) VALUES (1);
            """
        )
        for version in range(2, 8):
            connection.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
    db.initialize()
    assert db.health()["ok"] is True
    asset_columns = {row[1] for row in db.connect().execute("PRAGMA table_info(asset)").fetchall()}
    assert {"first_seen", "last_seen", "sources_json"} <= asset_columns
    assert next(db.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='asset_relation'"), None)
    assert next(db.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='asset_conflict'"), None)
    # existing rows survive the upgrade
    old = next(db.rows("SELECT * FROM asset WHERE asset_id='asset-old'"))
    assert old["normalized_value"] == "example.com"
