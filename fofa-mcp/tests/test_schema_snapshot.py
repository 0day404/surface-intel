from __future__ import annotations

import asyncio

from fofa_mcp.server import mcp


def test_mcp_tool_schema_snapshot():
    tools = {tool.name: tool.model_dump() for tool in asyncio.run(mcp.list_tools())}
    assert list(tools) == [
        "fofa_search",
        "fofa_search_structured",
        "fofa_search_next",
        "fofa_stats",
        "fofa_host",
        "fofa_account_info",
        "fofa_health",
    ]
    search = tools["fofa_search"]["inputSchema"]
    assert search["properties"]["size"]["default"] == 100
    assert search["properties"]["format"]["default"] == "standard"
    assert search["properties"]["task_id"]["default"] == "default"
    assert "query" in search["required"]
    assert "confirm" in search["properties"]
    assert "request_id" in search["properties"]

    structured = tools["fofa_search_structured"]["inputSchema"]
    assert structured["properties"]["domain"]["default"] == ""
    assert structured["properties"]["size"]["default"] == 100

    cursor = tools["fofa_search_next"]["inputSchema"]
    assert cursor["properties"]["full"]["default"] is False
    assert cursor["properties"]["cursor_page"]["default"] == 1
    assert "next_token" in cursor["properties"]
