import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.fofa import FofaMcpAdapter


@pytest.mark.asyncio
async def test_fofa_adapter_unpacks_real_standard_response():
    async def call_tool(name, arguments):
        assert name == "fofa_search"
        assert arguments["confirm"] is True
        return {
            "ok": True,
            "request_id": "req-1",
            "data": [{"host": "www.example.com"}],
            "pagination": {"next_token": "next-1"},
            "collection": {"has_more": True},
            "cost": {"consumed_fpoint": 2, "required_fpoints": 1},
        }

    result = await FofaMcpAdapter(call_tool).execute(
        {"query": 'domain="example.com"', "confirm": True},
        TaskContext("task-1", "scope-v1"),
    )

    assert result.status == "completed"
    assert result.observations == [{"host": "www.example.com"}]
    assert result.next_token == "next-1"
    assert result.has_more is True
    assert result.consumed_fpoints == 2
    assert result.required_fpoints == 1
