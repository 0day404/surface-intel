from surface_intel.normalization import normalize_target, normalize_targets
from surface_intel.policy.scope import Scope


def test_normalization_is_idempotent_and_supports_idna_ipv6_cidr():
    assert normalize_target("例子.测试").normalized_value == "xn--fsqu00a.xn--0zwm56d"
    assert normalize_target("2001:0db8::1").normalized_value == "2001:db8::1"
    assert normalize_target("10.0.0.1/24").normalized_value == "10.0.0.0/24"
    assert len(normalize_targets(["EXAMPLE.com.", "example.com"])) == 1


def test_exclusion_wins_and_active_level_is_gated():
    scope = Scope(
        authorized_domains=("example.com", "*.example.com"),
        excluded_targets=("admin.example.com",),
        confirmed=True,
    )
    assert scope.is_in_scope("www.example.com")
    assert not scope.is_in_scope("admin.example.com")
    assert scope.is_action_allowed(target="www.example.com", active=True)[0] is False
