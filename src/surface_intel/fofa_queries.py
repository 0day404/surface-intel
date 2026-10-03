from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueryBatch:
    query: str
    batch_index: int
    split_reason: str | None = None


def split_fofa_query(query: str, max_length: int) -> list[QueryBatch]:
    if max_length < 8:
        raise ValueError("max_length_too_small")
    normalized = " ".join(query.split())
    if len(normalized) <= max_length:
        return [QueryBatch(normalized, 1)]
    quoted = False
    parts: list[str] = []
    start = 0
    i = 0
    while i < len(normalized):
        char = normalized[i]
        if char == '"':
            quoted = not quoted
        if not quoted and normalized[i:i + 2] == "&&":
            parts.append(normalized[start:i].strip())
            start = i + 2
            i += 2
            continue
        i += 1
    parts.append(normalized[start:].strip())
    parts = [part for part in parts if part]
    batches: list[QueryBatch] = []
    current_query = ""
    for term in parts:
        candidate = term if not current_query else f"{current_query} && {term}"
        if current_query and len(candidate) > max_length:
            batches.append(QueryBatch(current_query, len(batches) + 1, "max_query_length"))
            current_query = term
        else:
            current_query = candidate
    if current_query:
        batches.append(QueryBatch(current_query, len(batches) + 1, "max_query_length"))
    if any(len(batch.query) > max_length for batch in batches):
        raise ValueError("query_term_exceeds_max_length")
    return batches
