import pytest

from surface_intel.fofa_runner import run_fofa_query, run_fofa_query_batches
from surface_intel.adapters.base import TaskContext
from surface_intel.state.database import Database


@pytest.mark.asyncio
async def test_fofa_runner_persists_pages_and_does_not_mark_intermediate_page_complete(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")

    calls = []

    async def call_tool(name, args):
        calls.append(name)
        if name == "fofa_search":
            return {"status": "completed", "results": [{"host": "a.example.com"}],
                    "has_more": True, "next_token": "n1", "request_id": "r1", "cost_units": 1}
        return {"status": "completed", "results": [{"host": "b.example.com"}],
                "has_more": False, "request_id": "r2", "cost_units": 1}

    result = await run_fofa_query(
        db, call_tool, task_id="t", scope_version="s",
        query='domain="example.com"', context=TaskContext("t", "s"),
    )
    assert result.status == "completed"
    assert result.observations == 2
    assert calls == ["fofa_search", "fofa_search_next"]
    batch = next(db.rows("SELECT status,cost_units,result_count FROM fofa_query_batch"))
    assert tuple(batch) == ("completed", 2, 2)
    assert next(db.rows("SELECT next_token FROM fofa_cursor"))["next_token"] == "n1"


@pytest.mark.asyncio
async def test_fofa_runner_rejects_has_more_without_token(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")

    async def call_tool(name, args):
        return {"status": "completed", "results": [], "has_more": True, "request_id": "r1"}

    result = await run_fofa_query(
        db, call_tool, task_id="t", scope_version="s",
        query='domain="example.com"', context=TaskContext("t", "s"),
    )
    assert result.status == "failed"
    assert result.error == "missing_next_token"


@pytest.mark.asyncio
async def test_fofa_query_batches_persist_plan_parent_and_split_reason(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute("INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")

    async def call_tool(name, args):
        return {"status": "completed", "results": [], "has_more": False, "request_id": name}

    results = await run_fofa_query_batches(
        db, call_tool, task_id="t", scope_version="s",
        query='domain="one.example.com" && domain="two.example.com"',
        context=TaskContext("t", "s"), max_query_length=25,
    )

    assert len(results) == 2
    plan = next(db.rows("SELECT parent_query_id,purpose FROM fofa_query_plan"))
    assert plan["parent_query_id"].startswith("fofa-query-")
    assert plan["purpose"] == "domain_assets"
    batches = list(db.rows("SELECT parent_query_id,split_reason FROM fofa_query_batch ORDER BY batch_index"))
    assert all(row["parent_query_id"] == plan["parent_query_id"] for row in batches)
    assert all(row["split_reason"] == "max_query_length" for row in batches)
