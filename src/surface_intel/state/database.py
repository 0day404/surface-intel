from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 7


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS task (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    scope_version TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS scope_snapshot (
                    scope_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    scope_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(task_id, scope_version)
                );
                CREATE TABLE IF NOT EXISTS action (
                    action_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    adapter TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scope_version TEXT NOT NULL,
                    result_ref TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS event (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS checkpoint (
                    checkpoint_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    source TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    redaction_status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS observation (
                    observation_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    evidence_id TEXT REFERENCES evidence(evidence_id),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS asset (
                    asset_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    asset_type TEXT NOT NULL,
                    normalized_value TEXT NOT NULL,
                    scope_status TEXT NOT NULL,
                    architecture_status TEXT NOT NULL DEFAULT 'unknown',
                    confidence REAL NOT NULL DEFAULT 0,
                    UNIQUE(task_id, asset_type, normalized_value)
                );
                CREATE TABLE IF NOT EXISTS gap (
                    gap_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    priority TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open'
                );
                CREATE TABLE IF NOT EXISTS adapter_registry (
                    adapter_name TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    health_status TEXT NOT NULL DEFAULT 'unknown',
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS adapter_call (
                    call_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    action_id TEXT,
                    adapter_name TEXT NOT NULL,
                    adapter_version TEXT NOT NULL,
                    request_id TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    input_summary TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS budget (
                    budget_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    kind TEXT NOT NULL,
                    limit_units INTEGER NOT NULL,
                    reserved_units INTEGER NOT NULL DEFAULT 0,
                    consumed_units INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(task_id, kind)
                );
                CREATE TABLE IF NOT EXISTS fofa_query_batch (
                    batch_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    query_fingerprint TEXT NOT NULL,
                    query TEXT NOT NULL,
                    batch_index INTEGER NOT NULL,
                    parent_query_id TEXT,
                    split_reason TEXT,
                    status TEXT NOT NULL,
                    scope_version TEXT NOT NULL,
                    request_id TEXT,
                    cursor TEXT,
                    next_token TEXT,
                    cost_units INTEGER NOT NULL DEFAULT 0,
                    estimated_fpoints INTEGER NOT NULL DEFAULT 0,
                    consumed_fpoints INTEGER NOT NULL DEFAULT 0,
                    result_count INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    UNIQUE(task_id, query_fingerprint, batch_index)
                );
                CREATE TABLE IF NOT EXISTS fofa_cursor (
                    cursor_id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL REFERENCES fofa_query_batch(batch_id),
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    query_fingerprint TEXT NOT NULL,
                    next_token TEXT NOT NULL,
                    cursor_page INTEGER NOT NULL,
                    request_id TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(batch_id, next_token)
                );
                CREATE TABLE IF NOT EXISTS fofa_query_plan (
                    plan_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES task(task_id),
                    parent_query_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    query_fingerprint TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    target_ref TEXT NOT NULL,
                    expected_fields_json TEXT NOT NULL,
                    source_basis_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'planned',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(task_id, parent_query_id)
                );
                CREATE TABLE IF NOT EXISTS subtask (
                    subtask_id TEXT PRIMARY KEY,
                    parent_task_id TEXT NOT NULL REFERENCES task(task_id),
                    role TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    scope_version TEXT NOT NULL,
                    input_snapshot_ref TEXT,
                    allowed_tools_json TEXT NOT NULL,
                    forbidden_actions_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    result_ref TEXT,
                    checkpoint_ref TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    last_heartbeat_at TEXT,
                    error_code TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_lease (
                    lease_id TEXT PRIMARY KEY,
                    subtask_id TEXT NOT NULL REFERENCES subtask(subtask_id),
                    worker_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_heartbeat_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    UNIQUE(subtask_id, status)
                );
                CREATE TABLE IF NOT EXISTS subtask_result (
                    result_id TEXT PRIMARY KEY,
                    subtask_id TEXT NOT NULL REFERENCES subtask(subtask_id),
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT OR IGNORE INTO schema_version(version) VALUES (1);
                """
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(action)").fetchall()}
            for name, definition in (
                ("attempt", "INTEGER NOT NULL DEFAULT 0"),
                ("started_at", "TEXT"),
                ("completed_at", "TEXT"),
                ("error_code", "TEXT"),
                ("phase", "TEXT NOT NULL DEFAULT 'unknown'"),
                ("target_ref", "TEXT"),
                ("input_json", "TEXT NOT NULL DEFAULT '{}'" ),
                ("cost_units", "INTEGER NOT NULL DEFAULT 0"),
                ("request_id", "TEXT"),
                ("next_cursor_ref", "TEXT"),
                ("external_state", "TEXT NOT NULL DEFAULT 'not_started'"),
                ("last_error_code", "TEXT"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE action ADD COLUMN {name} {definition}")
            batch_columns = {row[1] for row in db.execute("PRAGMA table_info(fofa_query_batch)").fetchall()}
            for name, definition in (
                ("estimated_fpoints", "INTEGER NOT NULL DEFAULT 0"),
                ("consumed_fpoints", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in batch_columns:
                    db.execute(f"ALTER TABLE fofa_query_batch ADD COLUMN {name} {definition}")
            db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (2)")
            db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (3)")
            db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (4)")
            db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (5)")
            db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (6)")
            db.execute("INSERT OR IGNORE INTO schema_version(version) VALUES (7)")

    def health(self) -> dict[str, object]:
        with self.connect() as db:
            foreign_keys = db.execute("PRAGMA foreign_keys").fetchone()[0]
            version = db.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            return {
                "ok": foreign_keys == 1 and version == SCHEMA_VERSION,
                "schema_version": version,
                "foreign_keys": bool(foreign_keys),
                "path": str(self.path),
            }

    def rows(self, query: str, params: tuple = ()) -> Iterator[sqlite3.Row]:
        with self.connect() as db:
            yield from db.execute(query, params).fetchall()
