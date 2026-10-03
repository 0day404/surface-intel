import pytest

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.capabilities import CapabilityRegistry
from surface_intel.adapters.safe_http import SafeHttpAdapter
from surface_intel.adapters.safe_port import SafePortAdapter


@pytest.mark.asyncio
async def test_safe_http_requires_authorized_context_and_get_only():
    async def transport(target):
        return {"status_code": 200, "response_size": 12}

    adapter = SafeHttpAdapter(transport)
    blocked = await adapter.execute({"target": "https://example.test", "method": "POST"}, TaskContext("t", "s"))
    assert blocked.status == "blocked"
    context = TaskContext("t", "s", {"scope_confirmed": True, "active_level": "safe_discovery"})
    result = await adapter.execute({"target": "https://example.test"}, context)
    assert result.observations[0]["status_code"] == 200


@pytest.mark.asyncio
async def test_safe_port_rejects_unauthorized_range_and_allows_known_port():
    async def connector(target, port):
        return {"transport_status": "open", "service": "https"}

    adapter = SafePortAdapter(connector)
    context = TaskContext("t", "s", {"scope_confirmed": True, "active_level": "safe_discovery", "allowed_ports": [443]})
    assert (await adapter.execute({"target": "example.test", "port": 80}, context)).status == "blocked"
    assert (await adapter.execute({"target": "example.test", "port": 443, "scan_range": True}, context)).status == "blocked"
    assert (await adapter.execute({"target": "example.test", "port": 443}, context)).status == "completed"


def test_capability_matrix_blocks_missing_scope_and_level():
    registry = CapabilityRegistry([SafeHttpAdapter.manifest])
    assert not registry.check("safe_http", capability="http_observation", active_level="passive_only", scope_confirmed=True).ok
    assert not registry.check("safe_http", capability="http_observation", active_level="safe_discovery", scope_confirmed=False).ok
    assert registry.check("safe_http", capability="http_observation", active_level="safe_discovery", scope_confirmed=True).ok
