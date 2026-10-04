import pytest



def test_default_fofa_root_resolution_order(monkeypatch, tmp_path):
    """可迁移部署:env > 同级 fofa-mcp 目录 > 历史固定路径,三级解析。"""
    from surface_intel import fofa_client

    # env 最优先
    monkeypatch.setenv("SURFACE_INTEL_FOFA_ROOT", str(tmp_path))
    assert fofa_client._default_fofa_root() == tmp_path

    # 同级 fofa-mcp(以 pyproject.toml 为存在标志)次之
    monkeypatch.delenv("SURFACE_INTEL_FOFA_ROOT")
    fake_pkg = tmp_path / "src" / "surface_intel"
    fake_pkg.mkdir(parents=True)
    sibling = tmp_path / "fofa-mcp"
    sibling.mkdir()
    (sibling / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(fofa_client, "__file__", str(fake_pkg / "fofa_client.py"))
    assert fofa_client._default_fofa_root() == sibling

    # 两侧都没有 → 兜底历史固定路径
    (sibling / "pyproject.toml").unlink()
    assert fofa_client._default_fofa_root() == fofa_client._LEGACY_FOFA_ROOT


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
