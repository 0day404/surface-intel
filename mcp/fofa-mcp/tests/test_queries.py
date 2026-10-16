import pytest

from fofa_mcp.errors import DuplicateQueryError, PaginationError
from fofa_mcp.queries import CursorTracker, QueryTracker, query_fingerprint


def test_query_fingerprint_is_stable_and_distinguishes_pages():
    common = {
        "query": 'domain="example.com"',
        "fields": ["ip", "port"],
        "mode": "page",
        "size": 100,
    }
    assert query_fingerprint(**common, page=1) == query_fingerprint(**common, page=1)
    assert query_fingerprint(**common, page=1) != query_fingerprint(**common, page=2)


def test_query_tracker_rejects_duplicate_per_task_only():
    tracker = QueryTracker()
    tracker.reserve("one", "same")
    tracker.reserve("two", "same")
    with pytest.raises(DuplicateQueryError):
        tracker.reserve("one", "same")


def test_cursor_tracker_binds_token_to_query_and_page():
    tracker = CursorTracker()
    tracker.register(
        "task",
        "token-1",
        query='domain="example.com"',
        fields=["ip"],
        full=False,
        next_page=2,
    )
    tracker.validate(
        "task",
        "token-1",
        query='domain="example.com"',
        fields=["ip"],
        full=False,
        cursor_page=2,
    )
    with pytest.raises(Exception):
        tracker.validate(
            "task",
            "token-1",
            query='domain="other.example"',
            fields=["ip"],
            full=False,
            cursor_page=2,
        )


def test_cursor_tracker_evicts_oldest_beyond_cap(monkeypatch):
    from fofa_mcp import queries as queries_module

    monkeypatch.setattr(queries_module, "MAX_CURSORS", 2)
    tracker = CursorTracker()
    for index in range(3):
        tracker.register(
            "t", f"tok-{index}", query="q", fields=["ip"], full=False, next_page=2
        )
    tracker.validate("t", "tok-1", query="q", fields=["ip"], full=False, cursor_page=2)
    with pytest.raises(PaginationError):
        tracker.validate("t", "tok-0", query="q", fields=["ip"], full=False, cursor_page=2)


def test_query_tracker_evicts_oldest_task_beyond_cap(monkeypatch):
    from fofa_mcp import queries as queries_module

    monkeypatch.setattr(queries_module, "MAX_TRACKED_TASKS", 2)
    tracker = QueryTracker()
    for task in ("a", "b", "c"):
        tracker.reserve(task, "fp")
    assert "a" not in tracker._seen
    tracker.reserve("c", "fp-2")
    with pytest.raises(DuplicateQueryError):
        tracker.reserve("c", "fp-2")
