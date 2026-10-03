"""R5.2: task/phase/target/adapter four-level budgets.

Reservations are atomic across matched levels (partial reservations roll
back), concurrent reservers cannot oversell the last unit, and committed
consumption survives failed actions - spent facts are never refunded.
"""
import asyncio

import pytest

from surface_intel.adapters.base import AdapterManifest, AdapterResult
from surface_intel.execution import AdapterExecutor, ExecutionContext
from surface_intel.state.budget import BudgetLedger, BudgetSpec
from surface_intel.state.database import Database


class CountingAdapter:
    def __init__(self, result=None):
        self.manifest = AdapterManifest("counting", "1.0", ("count",), cost_units=1)
        self.result = result or AdapterResult("completed")
        self.executed = 0

    async def validate(self, action, context):
        return AdapterResult("completed")

    async def execute(self, action, context):
        self.executed += 1
        return self.result

    async def cancel(self, action, context):
        return AdapterResult("cancelled")


def _setup(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','v1')")
    return db


def _budget_rows(db):
    return {row["kind"]: dict(row) for row in db.rows("SELECT * FROM budget WHERE task_id='t'")}


def test_four_levels_reserve_and_commit_atomically(tmp_path):
    db = _setup(tmp_path)
    ledger = BudgetLedger(db, "t", [
        BudgetSpec("task", "total", 10),
        BudgetSpec("phase", "collecting_passive", 5),
        BudgetSpec("target", "www.example.com", 2),
        BudgetSpec("adapter", "counting", 3),
    ])
    reserved = ledger.reserve(phase="collecting_passive", target="www.example.com",
                              adapter="counting", units=1)
    # all four matched levels reserved
    assert reserved == ["task:total", "phase:collecting_passive",
                        "target:www.example.com", "adapter:counting"]
    rows = _budget_rows(db)
    assert all(row["reserved_units"] == 1 for row in rows.values())
    ledger.commit(reserved, 1)
    rows = _budget_rows(db)
    assert all(row["consumed_units"] == 1 and row["reserved_units"] == 0
               for row in rows.values())


def test_exhausted_level_blocks_even_when_others_have_room(tmp_path):
    db = _setup(tmp_path)
    ledger = BudgetLedger(db, "t", [
        BudgetSpec("task", "total", 10),
        BudgetSpec("adapter", "counting", 1),
    ])
    assert ledger.reserve(adapter="counting", units=1) is not None
    # adapter level exhausted while the task level still has 9 units
    assert ledger.reserve(adapter="counting", units=1) is None
    rows = _budget_rows(db)
    assert rows["task:total"]["reserved_units"] == 1  # untouched by the refusal
    assert rows["adapter:counting"]["reserved_units"] == 1
    assert rows["adapter:counting"]["consumed_units"] == 0  # nothing committed here


def test_target_budgets_are_isolated(tmp_path):
    db = _setup(tmp_path)
    ledger = BudgetLedger(db, "t", [
        BudgetSpec("target", "www.example.com", 1),
    ])
    assert ledger.reserve(target="www.example.com", units=1) is not None
    assert ledger.reserve(target="www.example.com", units=1) is None
    assert ledger.reserve(target="other.example.com", units=1) is not None  # no budget -> unrestricted


def test_partial_reservation_rolls_back_atomically(tmp_path):
    db = _setup(tmp_path)
    ledger = BudgetLedger(db, "t", [
        BudgetSpec("task", "total", 10),
        BudgetSpec("phase", "collecting_passive", 5),
        BudgetSpec("target", "www.example.com", 0),  # always exhausted
    ])
    result = ledger.reserve(phase="collecting_passive", target="www.example.com", units=1)
    assert result is None
    rows = _budget_rows(db)
    # task and phase partial reservations were rolled back
    assert rows["task:total"]["reserved_units"] == 0
    assert rows["phase:collecting_passive"]["reserved_units"] == 0


@pytest.mark.asyncio
async def test_executor_ledger_blocks_and_keeps_spent_facts_on_failure(tmp_path):
    db = _setup(tmp_path)
    ledger = BudgetLedger(db, "t", [
        BudgetSpec("task", "total", 2),
        BudgetSpec("adapter", "counting", 2),
    ])
    from surface_intel.adapters.capabilities import CapabilityRegistry

    ok = CountingAdapter()
    executor = AdapterExecutor(db, CapabilityRegistry([ok.manifest]))
    context = ExecutionContext(task_id="t", scope_version="v1", scope_confirmed=True,
                               budget_ledger=ledger)
    for _ in range(2):
        result = await executor.execute(ok, {"action_id": "a", "target": "x",
                                             "phase": "collecting_passive",
                                             "capability": "count"}, context)
        assert result.status == "completed"
    blocked = await executor.execute(ok, {"action_id": "a", "target": "x",
                                          "phase": "collecting_passive",
                                          "capability": "count"}, context)
    assert blocked.status == "blocked"
    assert blocked.error == "budget_exhausted"
    rows = _budget_rows(db)
    assert rows["task:total"]["consumed_units"] == 2
    assert ok.executed == 2

    # failed actions still commit consumption - no fake refunds
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('u','planned','1.0','v1')")
    failing = CountingAdapter(AdapterResult("failed", error="boom"))
    executor2 = AdapterExecutor(db, CapabilityRegistry([failing.manifest]))
    ledger2 = BudgetLedger(db, "u", [BudgetSpec("task", "total", 5)])
    context2 = ExecutionContext(task_id="u", scope_version="v1", scope_confirmed=True,
                                budget_ledger=ledger2)
    result = await executor2.execute(failing, {"action_id": "a", "target": "x",
                                               "capability": "count"}, context2)
    assert result.status == "failed"
    rows = _budget_rows(db)
    assert rows["task:total"]["consumed_units"] == 2  # task t untouched
    u_rows = {row["kind"]: dict(row) for row in db.rows("SELECT * FROM budget WHERE task_id='u'")}
    assert u_rows["task:total"]["consumed_units"] == 1


def test_concurrent_reservers_cannot_oversell_last_unit(tmp_path):
    db = _setup(tmp_path)
    ledger = BudgetLedger(db, "t", [BudgetSpec("task", "total", 1)])
    from concurrent.futures import ThreadPoolExecutor

    def attempt(_):
        return ledger.reserve(units=1)

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(attempt, range(4)))
    assert outcomes.count(None) == 3  # exactly one winner
    winners = [o for o in outcomes if o is not None]
    assert len(winners) == 1
    rows = _budget_rows(db)
    assert rows["task:total"]["reserved_units"] == 1
    assert rows["task:total"]["consumed_units"] == 0


def test_invalid_budget_specs_rejected(tmp_path):
    db = _setup(tmp_path)
    with pytest.raises(ValueError, match="invalid_budget_level"):
        BudgetSpec("galaxy", "x", 1)
    with pytest.raises(ValueError, match="invalid_budget_key"):
        BudgetSpec("task", "  ", 1)
