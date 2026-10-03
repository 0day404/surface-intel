import pytest

from surface_intel.fofa_client import FofaStdioClient, unwrap_mcp_result


class FakeTransport:
    def __init__(self):
        self.requests = []
        self.notifications = []

    async def request(self, method, params=None):
        self.requests.append((method, params))
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}
        return {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": '{"ok": true, "request_id": "r1"}'}]},
        }

    async def notify(self, method, params=None):
        self.notifications.append((method, params))


@pytest.mark.asyncio
async def test_stdio_client_initializes_once_and_calls_tool():
    transport = FakeTransport()
    client = FofaStdioClient(transport)
    result = await client.call_tool("fofa_health", {})
    again = await client.call_tool("fofa_search", {"query": 'domain="example.com"'})
    assert result["ok"] is True
    assert again["request_id"] == "r1"
    assert [item[0] for item in transport.requests] == ["initialize", "tools/call", "tools/call"]
    assert transport.notifications == [("notifications/initialized", {})]


def test_unwrap_mcp_result_rejects_json_rpc_error():
    with pytest.raises(RuntimeError, match="bad request"):
        unwrap_mcp_result({"error": {"message": "bad request"}})
