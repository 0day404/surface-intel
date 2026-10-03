from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .base import AdapterManifest


@dataclass(frozen=True)
class CapabilityCheck:
    adapter: str
    ok: bool
    reasons: tuple[str, ...] = ()


class CapabilityRegistry:
    def __init__(self, manifests: Iterable[AdapterManifest] = ()):
        self._manifests = {manifest.name: manifest for manifest in manifests}

    def register(self, manifest: AdapterManifest) -> None:
        self._manifests[manifest.name] = manifest

    def get(self, name: str) -> AdapterManifest | None:
        return self._manifests.get(name)

    def check(
        self,
        name: str,
        *,
        capability: str,
        active_level: str,
        scope_confirmed: bool,
    ) -> CapabilityCheck:
        manifest = self.get(name)
        if manifest is None:
            return CapabilityCheck(name, False, ("adapter_not_registered",))
        reasons: list[str] = []
        if capability not in manifest.capabilities:
            reasons.append("capability_not_supported")
        levels = ("passive_only", "safe_discovery", "safe_probe", "intrusive_test")
        if levels.index(active_level) < levels.index(manifest.required_active_level):
            reasons.append("active_level_insufficient")
        if manifest.external_side_effects and not scope_confirmed:
            reasons.append("scope_not_confirmed")
        return CapabilityCheck(name, not reasons, tuple(reasons))

    def snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "name": item.name,
                "version": item.version,
                "capabilities": list(item.capabilities),
                "required_active_level": item.required_active_level,
                "cost_units": item.cost_units,
                "supports_pagination": item.supports_pagination,
                "supports_resume": item.supports_resume,
                "supports_cancellation": item.supports_cancellation,
                "external_side_effects": item.external_side_effects,
                "retry_policy": item.retry_policy,
                "sensitive_data_policy": item.sensitive_data_policy,
            }
            for item in self._manifests.values()
        ]
