from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1


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
                INSERT OR IGNORE INTO schema_version(version) VALUES (1);
                """
            )

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
