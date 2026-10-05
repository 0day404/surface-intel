"""P02: an odd double-quote count means the query lost its quoting on the way
through a Windows shell - refuse locally before any FOFA round-trip."""
import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.fofa_runner import run_fofa_query
from surface_intel.state.database import Database


@pytest.mark.asyncio
async def test_odd_quote_count_fails_fast_before_any_query(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0','s')")

    calls = []

    async def call_tool(name, args):
        calls.append(name)
        return {"status": "completed", "results": [], "has_more": False}

    with pytest.raises(ValueError, match="query_quote_mismatch"):
        await run_fofa_query(
            db, call_tool, task_id="t", scope_version="s",
            query='domain="example.com OR domain=example.net',  # one orphan quote
            context=TaskContext("t", "s"),
        )
    assert calls == []
    assert next(db.rows("SELECT COUNT(*) AS n FROM fofa_query_batch"))["n"] == 0
