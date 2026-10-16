import pytest

from fofa_mcp.errors import PolicyDeniedError
from fofa_mcp.policy import analyze_query, enforce_query_risk


def test_broad_query_requires_confirmation():
    analysis = analyze_query("*", fields=["ip"], size=100)
    assert analysis["risk"] == "high"
    with pytest.raises(PolicyDeniedError):
        enforce_query_risk(analysis, confirmed=False)


def test_full_query_risk_can_be_confirmed():
    analysis = analyze_query(
        'domain="example.com"',
        fields=["ip"],
        size=100,
        full=True,
    )
    assert analysis["requires_confirmation"] is True
    enforce_query_risk(analysis, confirmed=True)
