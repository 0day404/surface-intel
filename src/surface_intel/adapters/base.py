from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class AdapterManifest:
    name: str
    version: str
    capabilities: tuple[str, ...]
    required_active_level: str = "passive_only"
    cost_units: int = 0
    supports_pagination: bool = False
    supports_resume: bool = False
    supports_cancellation: bool = False
    external_side_effects: bool = False


@dataclass
class TaskContext:
    task_id: str
    scope_version: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterResult:
    status: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    next_token: str | None = None
    has_more: bool = False
    cost_units: int = 0
    request_id: str | None = None
    error: str | None = None


class Adapter(Protocol):
    manifest: AdapterManifest

    async def health(self, context: TaskContext) -> AdapterResult: ...
    async def validate(self, action: dict[str, Any], context: TaskContext) -> AdapterResult: ...
    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult: ...
    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult: ...
