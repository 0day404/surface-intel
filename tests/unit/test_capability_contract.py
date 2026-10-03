import pytest

from surface_intel.adapters.base import AdapterManifest
from surface_intel.adapters.capabilities import CapabilityRegistry
from surface_intel.states import CapabilityStatus


def test_manifest_defaults_to_available_and_serializes_status():
    manifest = AdapterManifest("fixture", "1.0", ("passive_observation",))
    assert manifest.capability_status == CapabilityStatus.AVAILABLE.value
    assert manifest.validate() == ()
    assert CapabilityRegistry([manifest]).snapshot()[0]["capability_status"] == "available"


def test_registry_blocks_degraded_adapter_explicitly():
    manifest = AdapterManifest(
        "fixture",
        "1.0",
        ("passive_observation",),
        capability_status=CapabilityStatus.DEGRADED.value,
    )
    check = CapabilityRegistry([manifest]).check(
        "fixture",
        capability="passive_observation",
        active_level="passive_only",
        scope_confirmed=True,
    )
    assert not check.ok
    assert "adapter_degraded" in check.reasons


def test_registry_rejects_invalid_manifest():
    registry = CapabilityRegistry()
    with pytest.raises(ValueError, match="invalid_adapter_manifest"):
        registry.register(AdapterManifest("fixture", "1.0", (), capability_status="bad"))
