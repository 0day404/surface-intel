from __future__ import annotations

import json
import uuid
from typing import Any

from ..states import ActionStatus
from .database import Database


class ActionRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_by_idempotency(self, key: str):
        return next(self.db.rows("SELECT * FROM action WHERE idempotency_key=?", (key,)), None)

    def claim(self, action_id: str) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """UPDATE action SET status=?, started_at=CURRENT_TIMESTAMP,
                   attempt=attempt+1, external_state='started', updated_at=CURRENT_TIMESTAMP
                   WHERE action_id=? AND status=?""",
                (ActionStatus.STARTED.value, action_id, ActionStatus.PLANNED.value),
            )
            return cursor.rowcount == 1

    def complete(self, action_id: str, result_ref: str | None = None, *,
                 request_id: str | None = None, cost_units: int = 0,
                 next_cursor_ref: str | None = None) -> bool:
        return self._terminal(action_id, ActionStatus.COMPLETED, result_ref, request_id, cost_units, next_cursor_ref)

    def fail(self, action_id: str, error_code: str, *, partial: bool = False) -> bool:
        return self._terminal(action_id, ActionStatus.PARTIAL if partial else ActionStatus.FAILED, None, None, 0, None, error_code)

    def unknown(self, action_id: str, error_code: str = "external_state_unknown") -> bool:
        return self._terminal(action_id, ActionStatus.UNKNOWN, None, None, 0, None, error_code)

    def cancel(self, action_id: str) -> bool:
        return self._terminal(action_id, ActionStatus.CANCELLED, None, None, 0, None, "cancelled")

    def _terminal(self, action_id: str, status: ActionStatus, result_ref: str | None,
                  request_id: str | None, cost_units: int, next_cursor_ref: str | None,
                  error_code: str | None = None) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """UPDATE action SET status=?, result_ref=?, request_id=?, cost_units=?,
                   next_cursor_ref=?, error_code=?, last_error_code=?, external_state=?,
                   completed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                   WHERE action_id=? AND status=?""",
                (status.value, result_ref, request_id, cost_units, next_cursor_ref,
                 error_code, error_code, status.value, action_id, ActionStatus.STARTED.value),
            )
            return cursor.rowcount == 1

    def create(self, task_id: str, adapter: str, scope_version: str,
               idempotency_key: str, payload: dict[str, Any] | None = None) -> str:
        action_id = f"action-{uuid.uuid4().hex[:16]}"
        with self.db.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO action
                   (action_id,task_id,idempotency_key,adapter,status,scope_version,input_json)
                   VALUES(?,?,?,?,?,?,?)""",
                (action_id, task_id, idempotency_key, adapter, ActionStatus.PLANNED.value,
                 scope_version, json.dumps(payload or {}, ensure_ascii=True, sort_keys=True)),
            )
        existing = self.get_by_idempotency(idempotency_key)
        return str(existing["action_id"])
