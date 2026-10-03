from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..states import CapabilityStatus


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
    retry_policy: str = "none"
    sensitive_data_policy: str = "redact_and_reject"
    capability_status: str = CapabilityStatus.AVAILABLE.value

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not self.name.strip():
            errors.append("name_required")
        if not self.version.strip():
            errors.append("version_required")
        if not self.capabilities:
            errors.append("capabilities_required")
        valid_levels = {"passive_only", "safe_discovery", "safe_probe", "intrusive_test"}
        if self.required_active_level not in valid_levels:
            errors.append("invalid_required_active_level")
        if self.cost_units < 0:
            errors.append("cost_units_must_be_non_negative")
        if self.capability_status not in {item.value for item in CapabilityStatus}:
            errors.append("invalid_capability_status")
        return tuple(errors)


@dataclass
class TaskContext:
    task_id: str
    scope_version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def scope_confirmed(self) -> bool:
        return bool(self.metadata.get("scope_confirmed", False))

    @property
    def active_level(self) -> str:
        return str(self.metadata.get("active_level", "passive_only"))


@dataclass
class AdapterResult:
    status: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    next_token: str | None = None
    has_more: bool = False
    cost_units: int = 0
    consumed_fpoints: int | None = None
    required_fpoints: int | None = None
    request_id: str | None = None
    error: str | None = None


class Adapter(Protocol):
    manifest: AdapterManifest

    async def health(self, context: TaskContext) -> AdapterResult: ...
    async def validate(self, action: dict[str, Any], context: TaskContext) -> AdapterResult: ...
    async def execute(self, action: dict[str, Any], context: TaskContext) -> AdapterResult: ...
    async def cancel(self, action: dict[str, Any], context: TaskContext) -> AdapterResult: ...
