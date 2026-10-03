from __future__ import annotations

import uuid
from typing import Any

from .database import Database


class FofaBatchRepository:
    def __init__(self, db: Database):
        self.db = db

    def create_batch(
        self,
        task_id: str,
        query_fingerprint: str,
        query: str,
        scope_version: str,
        *,
        batch_index: int = 1,
        parent_query_id: str | None = None,
        split_reason: str | None = None,
    ) -> str:
        batch_id = f"fofa-batch-{uuid.uuid4().hex[:16]}"
        with self.db.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO fofa_query_batch
                   (batch_id,task_id,query_fingerprint,query,batch_index,parent_query_id,
                    split_reason,status,scope_version)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (batch_id, task_id, query_fingerprint, query, batch_index, parent_query_id,
                 split_reason, "planned", scope_version),
            )
            row = connection.execute(
                """SELECT batch_id FROM fofa_query_batch
                   WHERE task_id=? AND query_fingerprint=? AND batch_index=?""",
                (task_id, query_fingerprint, batch_index),
            ).fetchone()
        return str(row["batch_id"])

    def start(self, batch_id: str) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                "UPDATE fofa_query_batch SET status='started' WHERE batch_id=? AND status='planned'",
                (batch_id,),
            )
            return cursor.rowcount == 1

    def complete(
        self,
        batch_id: str,
        *,
        request_id: str | None,
        next_token: str | None,
        cost_units: int,
        result_count: int,
        finished: bool = True,
    ) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """UPDATE fofa_query_batch SET status=?,request_id=?,next_token=?,
                   cost_units=?,result_count=? WHERE batch_id=? AND status='started'""",
                ("completed" if finished else "started", request_id, next_token,
                 cost_units, result_count, batch_id),
            )
            return cursor.rowcount == 1

    def fail(self, batch_id: str, error_code: str) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """UPDATE fofa_query_batch SET status='failed',error_code=?
                   WHERE batch_id=? AND status='started'""",
                (error_code, batch_id),
            )
            return cursor.rowcount == 1

    def save_cursor(
        self,
        batch_id: str,
        task_id: str,
        query_fingerprint: str,
        next_token: str,
        cursor_page: int,
        request_id: str | None,
    ) -> str:
        cursor_id = f"fofa-cursor-{uuid.uuid4().hex[:16]}"
        with self.db.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO fofa_cursor
                   (cursor_id,batch_id,task_id,query_fingerprint,next_token,cursor_page,request_id)
                   VALUES(?,?,?,?,?,?,?)""",
                (cursor_id, batch_id, task_id, query_fingerprint, next_token, cursor_page, request_id),
            )
            row = connection.execute(
                "SELECT cursor_id FROM fofa_cursor WHERE batch_id=? AND next_token=?",
                (batch_id, next_token),
            ).fetchone()
        return str(row["cursor_id"])

    def resume_cursor(self, batch_id: str) -> dict[str, Any] | None:
        row = next(
            self.db.rows(
                """SELECT * FROM fofa_cursor WHERE batch_id=? AND status='active'
                   ORDER BY cursor_page DESC LIMIT 1""",
                (batch_id,),
            ),
            None,
        )
        return dict(row) if row else None
