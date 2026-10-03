from surface_intel.policy.architecture import architecture_probe_decision, is_architecture_safe_for_probe


def test_third_party_and_unknown_are_blocked():
    for status in ("third_party", "origin_candidate", "unknown"):
        decision = architecture_probe_decision(
            architecture_status=status, scope_confirmed=True, active_level="safe_discovery"
        )
        assert not is_architecture_safe_for_probe(decision)
        assert decision.review_required


def test_confirmed_direct_allows_only_low_impact_actions():
    decision = architecture_probe_decision(
        architecture_status="confirmed_direct", scope_confirmed=True, active_level="safe_discovery"
    )
    assert is_architecture_safe_for_probe(decision)
    assert "credential_testing" in decision.blocked_actions
