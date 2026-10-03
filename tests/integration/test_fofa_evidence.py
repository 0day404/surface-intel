import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.fofa_runner import run_fofa_operation
from surface_intel.state.database import Database


@pytest.mark.asyncio
async def test_stats_operation_persists_evidence_without_asset_side_effect(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")

    async def call_tool(name, args):
        return {"status": "completed", "results": [{"field": "port", "value": "443", "count": 2}],
                "request_id": "stats-1", "cost_units": 1}

    result = await run_fofa_operation(
        db, call_tool, task_id="t", scope_version="s", tool="fofa_stats",
        arguments={"query": 'domain="example.com"'}, context=TaskContext("t", "s"),
    )
    assert result.status == "completed"
    assert next(db.rows("SELECT source FROM evidence"))["source"] == "fofa_mcp"
    assert next(db.rows("SELECT COUNT(*) AS count FROM asset"))["count"] == 0
