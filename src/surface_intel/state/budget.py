from __future__ import annotations

import uuid
from dataclasses import dataclass

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


BUDGET_LEVELS = ("task", "phase", "target", "adapter")


@dataclass(frozen=True)
class BudgetSpec:
    """One budget line: `task` level keys are free-form (e.g. "total"),
    phase/target/adapter keys name the phase, in-scope target or adapter."""

    level: str
    key: str
    limit_units: int

    def __post_init__(self):
        if self.level not in BUDGET_LEVELS:
            raise ValueError(f"invalid_budget_level:{self.level}")
        if not self.key.strip():
            raise ValueError("invalid_budget_key")


class BudgetLedger:
    """task/phase/target/adapter multi-level budgets over the budget table.

    Rows are stored with kind=f"{level}:{key}". A reservation is atomic across
    every matched level - either all levels reserve or none does (partial
    reservations are rolled back). Committed consumption is never refunded:
    failed and unknown actions keep their spent facts (plan R5).
    """

    def __init__(self, db: Database, task_id: str, specs: list[BudgetSpec]):
        self.db = db
        self.task_id = task_id
        self.specs: dict[tuple[str, str], BudgetSpec] = {}
        repo = BudgetRepository(db)
        for spec in specs:
            self.specs[(spec.level, spec.key)] = spec
            repo.create(task_id, self.kind_for(spec), spec.limit_units)

    @staticmethod
    def kind_for(spec: BudgetSpec) -> str:
        return f"{spec.level}:{spec.key}"

    def reserve(self, *, phase: str | None = None, target: str | None = None,
                adapter: str | None = None, units: int = 1) -> list[str] | None:
        """Atomically reserve `units` on every matched level.

        Returns the reserved budget kinds, or None when any level lacks room
        (partial reservations are released before returning).
        """
        wanted: list[str] = []
        task_spec = self.specs.get(("task", "total"))
        if task_spec is not None:
            wanted.append(self.kind_for(task_spec))
        if phase is not None:
            spec = self.specs.get(("phase", phase))
            if spec is not None:
                wanted.append(self.kind_for(spec))
        if target is not None:
            spec = self.specs.get(("target", target))
            if spec is not None:
                wanted.append(self.kind_for(spec))
        if adapter is not None:
            spec = self.specs.get(("adapter", adapter))
            if spec is not None:
                wanted.append(self.kind_for(spec))

        repo = BudgetRepository(self.db)
        reserved: list[str] = []
        for kind in wanted:
            if repo.reserve(self.task_id, kind, units):
                reserved.append(kind)
            else:
                for done in reserved:
                    repo.release(self.task_id, done, units)
                return None
        return reserved

    def commit(self, kinds: list[str], units: int = 1) -> None:
        repo = BudgetRepository(self.db)
        for kind in kinds:
            if not repo.commit(self.task_id, kind, units):
                raise RuntimeError(f"budget_commit_failed:{kind}")

    def release(self, kinds: list[str], units: int = 1) -> None:
        repo = BudgetRepository(self.db)
        for kind in kinds:
            repo.release(self.task_id, kind, units)
