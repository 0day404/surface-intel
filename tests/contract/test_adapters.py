import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.mock import MockFofaAdapter, MockPassiveAdapter


@pytest.mark.asyncio
async def test_mock_passive_returns_structured_empty_result():
    result = await MockPassiveAdapter().execute({"target": "example.com"}, TaskContext("task-1", "scope-v1"))
    assert result.status == "completed"
    assert result.observations[0]["status"] == "empty"


@pytest.mark.asyncio
async def test_mock_fofa_requires_consuming_next_pages():
    adapter = MockFofaAdapter([
        {"observations": [{"host": "a.example.com"}], "has_more": True, "next_token": "n1"},
        {"observations": [{"host": "b.example.com"}], "has_more": False},
    ])
    context = TaskContext("task-1", "scope-v1")
    first = await adapter.execute({"query": 'domain="example.com"', "page": 1}, context)
    second = await adapter.execute({"query": 'domain="example.com"', "page": 2, "next_token": first.next_token}, context)
    assert first.has_more and first.next_token == "n1"
    assert second.has_more is False
    assert [row["host"] for row in first.observations + second.observations] == ["a.example.com", "b.example.com"]
