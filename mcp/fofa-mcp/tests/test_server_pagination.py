from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fofa_mcp import server
from fofa_mcp.budget import BudgetRegistry
from fofa_mcp.config import AccountConfig, Settings
from fofa_mcp.queries import CursorTracker, QueryTracker

FIXTURES = Path(__file__).parent / "fixtures" / "fofa"
FIELDS = ["ip", "port", "protocol", "host"]
QUERY = 'domain="example.com"'


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _FakeClient:
    def __init__(self):
        self.next_calls: list[str] = []

    async def search(self, query, fields, size, page):
        data = _fixture("search_success.json")
        data["_account_used"] = "primary"
        return data

    async def search_next(self, query, fields, size, next_token, full):
        self.next_calls.append(next_token)
        data = _fixture("search_success.json")
        data["next"] = ""
        data["_account_used"] = "primary"
        return data


def _settings():
    return Settings(
        api_base="https://fofa.test",
        timeout_sec=1,
        default_page_size=100,
        max_page_size=200,
        max_cursor_pages=10,
        max_requests_per_session=50,
        max_estimated_fpoints=100,
        max_total_results=10000,
        max_query_length=4096,
        log_level="WARNING",
        max_retries=0,
        max_host_response_bytes=2_000_000,
        accounts=(AccountConfig("primary", "", "primary-key"),),
    )


def _install_service(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(
        server,
        "_service",
        (
            _settings(),
            fake,
            BudgetRegistry(max_requests=50, max_estimated_fpoints=100, max_total_results=10000),
            QueryTracker(),
            CursorTracker(),
        ),
    )
    return fake


def test_fofa_search_registers_next_token_for_continuation(monkeypatch):
    _install_service(monkeypatch)
    result = asyncio.run(
        server.fofa_search(query=QUERY, fields=FIELDS, size=2, task_id="t1")
    )
    assert result["ok"] is True
    assert result["pagination"]["next_token"] == "cursor-success"
    assert result["summary"]["has_more"] is True

    followed = asyncio.run(
        server.fofa_search_next(
            query=QUERY,
            next_token="cursor-success",
            fields=FIELDS,
            size=2,
            cursor_page=2,
            task_id="t1",
        )
    )
    assert followed["ok"] is True


def test_wrong_cursor_page_is_rejected(monkeypatch):
    _install_service(monkeypatch)
    asyncio.run(server.fofa_search(query=QUERY, fields=FIELDS, size=2, task_id="t1"))
    result = asyncio.run(
        server.fofa_search_next(
            query=QUERY,
            next_token="cursor-success",
            fields=FIELDS,
            size=2,
            cursor_page=3,
            task_id="t1",
        )
    )
    assert result["ok"] is False
    assert result["error"]["type"] == "pagination_error"


def test_structured_search_also_yields_continuable_token(monkeypatch):
    _install_service(monkeypatch)
    result = asyncio.run(
        server.fofa_search_structured(
            domain="example.com", fields=FIELDS, size=2, task_id="t2"
        )
    )
    assert result["ok"] is True
    assert result["pagination"]["next_token"] == "cursor-success"

    followed = asyncio.run(
        server.fofa_search_next(
            query='domain="example.com"',
            next_token="cursor-success",
            fields=FIELDS,
            size=2,
            cursor_page=2,
            task_id="t2",
        )
    )
    assert followed["ok"] is True
