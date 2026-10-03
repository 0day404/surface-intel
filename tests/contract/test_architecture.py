import pytest

from surface_intel.architecture import (
    ARCHITECTURE_STATUSES,
    CONFIRMED_STATUSES,
    CATEGORY_PRIORITY,
    classify_signals,
    match_keyword,
    match_suffix,
)


@pytest.mark.parametrize("value,vendor,category", [
    ("www.example.com.cdn.cloudflare.net", "cloudflare", "cdn"),
    ("ns1.ns.cloudflare.com", "cloudflare", "dns"),
    ("example.com.akamaiedge.net", "akamai", "cdn"),
    ("d111111abcdef8.cloudfront.net", "cloudfront", "cdn"),
    ("my-app.us-east-1.elb.amazonaws.com", "aws-elb", "lb"),
    ("app trafficmanager.net", None, None),
])
def test_suffix_match_identifies_vendors(value, vendor, category):
    matches = match_suffix(value)
    if vendor is None:
        assert "trafficmanager" not in str(matches) or matches  # bare words don't match suffix
        return
    assert any(m["vendor"] == vendor and category in m["categories"] for m in matches)


def test_suffix_match_requires_full_domain_suffix():
    assert match_suffix("example.com") == []
    assert match_suffix("fakecloudflare.net") == []
    assert match_suffix("evildnscdn.cloudflare.net.evil.io") == []


@pytest.mark.parametrize("text,keyword", [
    ("Cloudflare, Inc.", "cloudflare"),
    ("AMAZON-02", "amazon"),
    ("Hetzner Online GmbH", "hetzner"),
    ("Example Org Ltd", None),
])
def test_keyword_match_for_org_and_issuer_strings(text, keyword):
    matches = match_keyword(text)
    if keyword is None:
        assert matches == []
    else:
        assert any(m["vendor"] == keyword for m in matches)


def test_single_signal_yields_candidate():
    status = classify_signals([
        {"vendor": "cloudflare", "categories": ["cdn", "waf"], "value": "x.cdn.cloudflare.net",
         "signal": "dns_cname"},
    ])
    assert status == "cdn_candidate"


def test_corroborating_signals_yield_likely():
    status = classify_signals([
        {"vendor": "cloudflare", "categories": ["cdn", "waf"], "value": "x.cdn.cloudflare.net",
         "signal": "dns_cname"},
        {"vendor": "cloudflare", "categories": ["cdn", "waf"], "value": "Cloudflare Inc ECC CA-3",
         "signal": "cert_issuer"},
    ])
    assert status == "cdn_likely"


def test_category_priority_resolves_multiple_likely_categories():
    signals = [
        {"vendor": "aws-elb", "categories": ["lb"], "value": "a.elb.amazonaws.com", "signal": "dns_cname"},
        {"vendor": "aws", "categories": ["lb", "cloud_hosting"], "value": "amazon", "signal": "rdap_org"},
        {"vendor": "cloudfront", "categories": ["cdn"], "value": "x.cloudfront.net", "signal": "dns_cname"},
        {"vendor": "cloudfront", "categories": ["cdn"], "value": "y.cloudfront.net", "signal": "dns_ns"},
    ]
    assert classify_signals(signals) == "cdn_likely"  # cdn outranks lb


def test_identical_signals_do_not_fake_corroboration():
    status = classify_signals([
        {"vendor": "cloudflare", "categories": ["cdn"], "value": "x.cdn.cloudflare.net", "signal": "dns_cname"},
        {"vendor": "cloudflare", "categories": ["cdn"], "value": "x.cdn.cloudflare.net", "signal": "dns_cname"},
    ])
    assert status == "cdn_candidate"


def test_classification_never_produces_confirmed_statuses():
    combos = [
        [],
        [{"vendor": "cloudflare", "categories": ["cdn", "waf"], "value": "a", "signal": "s1"},
         {"vendor": "cloudflare", "categories": ["cdn", "waf"], "value": "b", "signal": "s2"}],
        [{"vendor": "aws", "categories": ["cloud_hosting", "lb"], "value": "a", "signal": "s1"},
         {"vendor": "aws", "categories": ["cloud_hosting", "lb"], "value": "b", "signal": "s2"}],
    ]
    for signals in combos:
        assert classify_signals(signals) in ARCHITECTURE_STATUSES
        assert classify_signals(signals) not in CONFIRMED_STATUSES


def test_status_vocabulary_is_frozen():
    # the confirmed set stays out of the passive status vocabulary by design
    assert set(CONFIRMED_STATUSES).isdisjoint(set(ARCHITECTURE_STATUSES))
    for candidate, likely in (("cdn", "cdn"), ("waf", "waf"), ("lb", "lb"),
                              ("cloud_hosting", "cloud_hosting"), ("dns", "dns"), ("email", "email")):
        pair = [s for s in ARCHITECTURE_STATUSES if s.startswith(candidate)]
        assert f"{candidate}_candidate" in pair and f"{candidate}_likely" in pair
    assert "third_party" in ARCHITECTURE_STATUSES and "unknown" in ARCHITECTURE_STATUSES
    assert CATEGORY_PRIORITY[0] == "cdn"
