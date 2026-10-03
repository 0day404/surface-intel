import pytest
from dataclasses import replace

from surface_intel.adapters.capabilities import CapabilityRegistry
from surface_intel.adapters.mock import MockPassiveAdapter
from surface_intel.execution import AdapterExecutor, ExecutionContext
from surface_intel.state.database import Database


@pytest.mark.asyncio
async def test_executor_records_call_and_blocks_without_scope(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('task-1','planned','1.0','scope-v1')")
    adapter = MockPassiveAdapter()
    executor = AdapterExecutor(db, CapabilityRegistry([adapter.manifest]))
    context = ExecutionContext("task-1", "scope-v1", scope_confirmed=False)

    result = await executor.execute(adapter, {"target": "example.com"}, context)

    assert result.status == "blocked"
    assert result.error == "scope_not_confirmed"
    call = next(db.rows("SELECT status,error_code,input_summary FROM adapter_call"))
    assert call["status"] == "blocked"
    assert call["error_code"] == "policy_blocked"
    assert "example.com" in call["input_summary"]


@pytest.mark.asyncio
async def test_executor_consumes_budget_before_adapter_execution(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('task-1','planned','1.0','scope-v1')")
    adapter = MockPassiveAdapter()
    adapter.manifest = replace(adapter.manifest, cost_units=1)
    executor = AdapterExecutor(db, CapabilityRegistry([adapter.manifest]))
    context = ExecutionContext("task-1", "scope-v1", scope_confirmed=True, budget={"cost_units": 0})

    result = await executor.execute(adapter, {"target": "example.com"}, context)

    assert result.status == "blocked"
    assert result.error == "budget_exhausted"
