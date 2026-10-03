import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.fofa import FofaMcpAdapter


@pytest.mark.asyncio
async def test_fofa_tool_contract_selects_search_and_next_and_preserves_request_id():
    calls = []

    async def call_tool(name, arguments):
        calls.append((name, arguments))
        if name == "fofa_search":
            return {
                "status": "completed",
                "results": [{"host": "a.example.com"}],
                "has_more": True,
                "next_token": "cursor-1",
                "cost_units": 2,
                "request_id": "req-1",
            }
        return {
            "status": "completed",
            "results": [{"host": "b.example.com"}],
            "has_more": False,
            "request_id": "req-2",
        }

    adapter = FofaMcpAdapter(call_tool)
    context = TaskContext("t", "scope-v1")
    first = await adapter.execute({"query": 'domain="example.com"'}, context)
    second = await adapter.execute(
        {"query": 'domain="example.com"', "next_token": first.next_token}, context
    )
    assert [call[0] for call in calls] == ["fofa_search", "fofa_search_next"]
    assert first.next_token == "cursor-1"
    assert second.has_more is False
    assert first.request_id == "req-1"


@pytest.mark.asyncio
async def test_fofa_health_reports_unconfigured_transport():
    async def call_tool(name, arguments):
        raise RuntimeError("session unavailable")

    result = await FofaMcpAdapter(call_tool).health(TaskContext("t", "scope-v1"))
    assert result.status == "unknown"
    assert result.error.startswith("not_configured:")
