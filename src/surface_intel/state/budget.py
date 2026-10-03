from __future__ import annotations

import uuid

from .database import Database


class BudgetRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, task_id: str, kind: str, limit_units: int) -> str:
        if limit_units < 0:
            raise ValueError("budget_limit_must_be_non_negative")
        budget_id = f"budget-{uuid.uuid4().hex[:16]}"
        with self.db.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO budget
                   (budget_id,task_id,kind,limit_units)
                   VALUES(?,?,?,?)""",
                (budget_id, task_id, kind, limit_units),
            )
            row = connection.execute(
                "SELECT budget_id FROM budget WHERE task_id=? AND kind=?", (task_id, kind)
            ).fetchone()
        return str(row["budget_id"])

    def reserve(self, task_id: str, kind: str, units: int) -> bool:
        if units < 0:
            return False
        with self.db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE budget SET reserved_units=reserved_units+?,
                   updated_at=CURRENT_TIMESTAMP
                   WHERE task_id=? AND kind=?
                   AND limit_units-consumed_units-reserved_units>=?""",
                (units, task_id, kind, units),
            )
            return cursor.rowcount == 1

    def commit(self, task_id: str, kind: str, units: int) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """UPDATE budget SET reserved_units=reserved_units-?,
                   consumed_units=consumed_units+?, updated_at=CURRENT_TIMESTAMP
                   WHERE task_id=? AND kind=? AND reserved_units>=?""",
                (units, units, task_id, kind, units),
            )
            return cursor.rowcount == 1

    def release(self, task_id: str, kind: str, units: int) -> bool:
        with self.db.connect() as connection:
            cursor = connection.execute(
                """UPDATE budget SET reserved_units=reserved_units-?,
                   updated_at=CURRENT_TIMESTAMP
                   WHERE task_id=? AND kind=? AND reserved_units>=?""",
                (units, task_id, kind, units),
            )
            return cursor.rowcount == 1
