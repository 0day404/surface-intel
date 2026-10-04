"""A1-3: scope consistency warnings surface the silent passive_only
downgrade at create time instead of at the first blocked active call."""
from surface_intel.policy.scope import scope_warnings


def test_active_without_level_warns():
    assert scope_warnings({"active_testing_allowed": True}) == [
        "active_testing_allowed_without_level"]


def test_invalid_level_warns_with_value():
    assert scope_warnings({"active_testing_allowed": True,
                           "active_testing_level": "full_pwn"}) == [
        "active_testing_level_invalid:full_pwn"]


def test_passive_only_contradiction_warns():
    assert scope_warnings({"active_testing_allowed": True,
                           "active_testing_level": "passive_only"}) == [
        "active_testing_allowed_but_passive_only"]


def test_valid_combinations_stay_silent():
    assert scope_warnings({"active_testing_allowed": True,
                           "active_testing_level": "safe_discovery"}) == []
    assert scope_warnings({}) == []
    assert scope_warnings({"active_testing_allowed": False}) == []
