import pytest



@pytest.mark.asyncio
async def test_stdio_transport_and_client_support_async_with():
    """Retrospective P0-3: async-with guarantees transport cleanup so driver
    authors never leak pipes (Windows Proactor warnings)."""
    from surface_intel.fofa_client import FofaStdioClient, StdioJsonRpcTransport

    class FakeTransport:
        def __init__(self):
            self.closed = False
            self.entered = False

        async def request(self, method, params=None):
            return {"result": {"content": [{"type": "text", "text": "{}"}]}}

        async def notify(self, method, params=None):
            return None

        async def close(self):
            self.closed = True

    transport = FakeTransport()
    client = FofaStdioClient(transport=transport)
    async with client as entered:
        assert entered is client
        await client.call_tool("fofa_health", {})
    assert transport.closed is True
