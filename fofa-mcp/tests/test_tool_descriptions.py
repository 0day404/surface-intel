import asyncio

from fofa_mcp.server import mcp


def test_tool_descriptions_explain_call_relationships():
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert "fofa_search_next" in tools["fofa_search"].description
    assert "fofa_stats" in tools["fofa_search"].description
    assert "fofa_host" in tools["fofa_search"].description
    assert "does not consume FOFA quota" in tools["fofa_health"].description
