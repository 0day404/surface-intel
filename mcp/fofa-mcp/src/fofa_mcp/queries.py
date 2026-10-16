from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .errors import DuplicateQueryError, PaginationError

# 进程内跟踪表上限；超出后淘汰最旧条目，防止长期运行的服务内存无界增长。
MAX_TRACKED_TASKS = 256
MAX_CURSORS = 1024


def _evict_oldest(mapping: dict, cap: int) -> None:
    while len(mapping) >= cap:
        mapping.pop(next(iter(mapping)))


def query_fingerprint(
    *,
    query: str,
    fields: list[str],
    mode: str,
    size: int,
    page: int | None = None,
    next_token: str = "",
    full: bool = False,
) -> str:
    payload = {
        "query": " ".join(query.split()),
        "fields": fields,
        "mode": mode,
        "size": size,
        "page": page,
        "next_token": next_token,
        "full": full,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


@dataclass
class QueryTracker:
    _seen: dict[str, set[str]] = field(default_factory=dict)

    def reserve(self, task_id: str, fingerprint: str) -> None:
        task_queries = self._seen.get(task_id)
        if task_queries is None:
            _evict_oldest(self._seen, MAX_TRACKED_TASKS)
            task_queries = self._seen.setdefault(task_id, set())
        if fingerprint in task_queries:
            raise DuplicateQueryError(
                f"query was already executed for task {task_id}"
            )
        task_queries.add(fingerprint)


@dataclass(frozen=True)
class CursorState:
    query: str
    fields: tuple[str, ...]
    full: bool
    next_page: int


@dataclass
class CursorTracker:
    _states: dict[tuple[str, str], CursorState] = field(default_factory=dict)

    def register(
        self,
        task_id: str,
        token: str,
        *,
        query: str,
        fields: list[str],
        full: bool,
        next_page: int,
    ) -> None:
        if token:
            if (task_id, token) not in self._states:
                _evict_oldest(self._states, MAX_CURSORS)
            self._states[(task_id, token)] = CursorState(
                query=" ".join(query.split()),
                fields=tuple(fields),
                full=full,
                next_page=next_page,
            )

    def validate(
        self,
        task_id: str,
        token: str,
        *,
        query: str,
        fields: list[str],
        full: bool,
        cursor_page: int,
    ) -> None:
        if not token:
            if cursor_page != 1:
                raise PaginationError("cursor_page must be 1 when next_token is empty")
            return
        state = self._states.get((task_id, token))
        if state is None:
            raise PaginationError("next_token is unknown, expired, or belongs to another task")
        if (
            state.query != " ".join(query.split())
            or state.fields != tuple(fields)
            or state.full != full
        ):
            raise PaginationError("next_token does not match query, fields, or full")
        if state.next_page != cursor_page:
            raise PaginationError(
                f"expected cursor_page {state.next_page}, got {cursor_page}"
            )
