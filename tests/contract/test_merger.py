import pytest

from surface_intel.merger import (
    RELATION_TYPES,
    asset_confidence,
    classify_asset_scope,
    normalize_asset_value,
    relation_strength,
)
from surface_intel.policy.scope import Scope


def test_domain_normalization_is_idempotent_and_case_insensitive():
    assert normalize_asset_value("domain", "WWW.Example.COM.") == "www.example.com"
    assert normalize_asset_value("domain", "www.example.com") == "www.example.com"
    assert normalize_asset_value("domain", "  ") is None


@pytest.mark.parametrize("raw,expected", [
    ("203.0.113.5", "203.0.113.5"),
    ("2001:0DB8::0001", "2001:db8::1"),
    ("not-an-ip", None),
    ("", None),
])
def test_ip_normalization_uses_canonical_representation(raw, expected):
    assert normalize_asset_value("ip", raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("203.0.113.0/24", "203.0.113.0/24"),
    ("203.0.113/24", "203.0.113.0/24"),  # registry shorthand, trailing zero dropped
    ("203.0/16", "203.0.0.0/16"),
    ("2001:db8::/32", "2001:db8::/32"),
    ("203.0.113.1/24", "203.0.113.0/24"),  # host bits normalized away
    ("nonsense/24", None),
    ("203.0.113.5", None),
])
def test_cidr_normalization_accepts_registry_shorthand(raw, expected):
    assert normalize_asset_value("cidr", raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("64512", "64512"),
    ("AS64512", "64512"),
    ("as-64512", None),
    ("", None),
])
def test_asn_normalization_is_numeric_only(raw, expected):
    assert normalize_asset_value("asn", raw) == expected


def test_organization_normalization_preserves_legal_form():
    assert normalize_asset_value("organization", " Example Org Ltd ") == "Example Org Ltd"


def test_relation_strength_taxonomy_matches_plan_section_12_2():
    for relation_type in ("dns_a", "dns_cname", "cert_name"):
        assert relation_strength(relation_type) == "strong"
    for relation_type in ("rdap_org", "rdap_cidr", "rdap_asn"):
        assert relation_strength(relation_type) == "medium"
    assert relation_strength("name_similarity_only") == "weak"
    assert "cert_name" in RELATION_TYPES


def test_scope_classification_attribution_classes_never_confirm():
    scope = Scope(authorized_domains=("example.com", "*.example.com"),
                  authorized_ips=("203.0.113.5",), confirmed=True)
    assert classify_asset_scope(scope, "domain", "www.example.com") == "confirmed"
    assert classify_asset_scope(scope, "domain", "other.net") == "candidate"
    assert classify_asset_scope(scope, "ip", "203.0.113.5") == "confirmed"
    assert classify_asset_scope(scope, "ip", "198.51.100.9") == "candidate"
    # organization, cidr and asn can never be confirmed from passive data
    assert classify_asset_scope(scope, "organization", "Example Org Ltd") == "candidate"
    assert classify_asset_scope(scope, "cidr", "203.0.113.0/24") == "candidate"
    assert classify_asset_scope(scope, "asn", "64512") == "candidate"


def test_confidence_formula_is_deterministic_and_bounded():
    # user-scoped anchors are fully trusted
    assert asset_confidence(scope_status="confirmed", scope_anchored=True, strengths=set(), source_count=1) == 1.0
    # single strong relation
    assert asset_confidence(scope_status="candidate", scope_anchored=False, strengths={"strong"}, source_count=1) == 0.7
    # multi-source corroboration: +0.1 per extra source, capped at +0.2
    assert asset_confidence(scope_status="candidate", scope_anchored=False, strengths={"strong"}, source_count=2) == 0.8
    assert asset_confidence(scope_status="candidate", scope_anchored=False, strengths={"strong"}, source_count=5) == 0.9
    # medium alone is weaker than strong alone
    assert asset_confidence(scope_status="candidate", scope_anchored=False, strengths={"medium"}, source_count=1) == 0.5
    # weak alone never reaches a confirmable score
    assert asset_confidence(scope_status="candidate", scope_anchored=False, strengths={"weak"}, source_count=1) == 0.25
    # candidates are capped below 1.0
    assert asset_confidence(scope_status="candidate", scope_anchored=False, strengths={"strong", "medium"}, source_count=5) == 0.9
    # authorized observations still report evidence-based confidence -
    # only the user scope anchor itself reaches 1.0
    assert asset_confidence(scope_status="confirmed", scope_anchored=False, strengths={"strong"}, source_count=4) == 0.9
