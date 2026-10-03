import pytest
from datetime import datetime, timedelta, timezone

from surface_intel.adapters.capabilities import CapabilityRegistry
from surface_intel.adapters.mock import MockPassiveAdapter
from surface_intel.execution import AdapterExecutor, ExecutionContext
from surface_intel.state.budget import BudgetRepository
from surface_intel.state.database import Database


def test_budget_reservation_is_bounded(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")
    budget = BudgetRepository(db)
    budget.create("t", "mock_passive", 1)
    assert budget.reserve("t", "mock_passive", 1)
    assert not budget.reserve("t", "mock_passive", 1)
    assert budget.commit("t", "mock_passive", 1)
    row = next(db.rows("SELECT reserved_units,consumed_units FROM budget WHERE task_id='t'"))
    assert tuple(row) == (0, 1)


@pytest.mark.asyncio
async def test_executor_blocks_out_of_scope_and_scope_version_mismatch(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")
    adapter = MockPassiveAdapter()
    executor = AdapterExecutor(db, CapabilityRegistry([adapter.manifest]))
    context = ExecutionContext(
        "t", "scope-v1", True,
        metadata={"scope_payload": {"confirmed": True, "scope_version": "scope-v1",
                                    "authorized_domains": ["allowed.example"]}},
    )
    out = await executor.execute(adapter, {"target": "other.example"}, context)
    assert out.status == "blocked"
    assert out.error == "target_out_of_scope"
    context.scope_version = "scope-v2"
    mismatch = await executor.execute(adapter, {"target": "allowed.example"}, context)
    assert mismatch.error == "scope_version_mismatch"


@pytest.mark.asyncio
async def test_executor_blocks_closed_scope_time_window(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")
    adapter = MockPassiveAdapter()
    executor = AdapterExecutor(db, CapabilityRegistry([adapter.manifest]))
    context = ExecutionContext(
        "t", "scope-v1", True,
        metadata={"scope_payload": {
            "confirmed": True, "scope_version": "scope-v1",
            "authorized_domains": ["allowed.example"],
            "ends_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        }},
    )
    result = await executor.execute(adapter, {"target": "allowed.example"}, context)
    assert result.status == "blocked"
    assert result.error == "outside_time_window"
