import json

import urllib.error

import pytest

from surface_intel.rdap_client import (
    RdapOrgClient,
    SequentialLookupClient,
    WhoisFallbackClient,
    normalize_rdap_record,
)


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        outcome = self.responses.get(url)
        if outcome is None:
            return 200, "{}"
        status, body = outcome
        if isinstance(body, Exception):
            raise body
        return status, body


def _client(responses, endpoint="https://rdap.org/"):
    fetch = FakeHttp(responses)
    return RdapOrgClient(endpoint=endpoint, timeout=5.0, fetch=fetch), fetch


def _rdap_domain_payload():
    return {
        "handle": "D1234-CNNIC",
        "ldhName": "example.com",
        "status": ["client transfer prohibited"],
        "nameservers": [{"ldhName": "NS1.Example.com."}],
        "events": [
            {"eventAction": "registration", "eventDate": "2020-01-01T00:00:00"},
            {"eventAction": "expiration", "eventDate": "2027-01-01T00:00:00"},
            {"eventAction": "last changed", "eventDate": "2026-01-01T00:00:00"},
        ],
        "entities": [
            {
                "roles": ["registrar"],
                "vcardArray": ["vcard", [
                    ["version", {}, "text", "4.0"],
                    ["fn", {}, "text", "Example Registrar Co."],
                ]],
            },
            {
                "roles": ["abuse"],
                "vcardArray": ["vcard", [
                    ["fn", {}, "text", "Abuse Contact"],
                    ["email", {}, "text", "abuse@registrar.example"],
                ]],
            },
            {
                "roles": ["registrant"],
                "vcardArray": ["vcard", [
                    ["fn", {}, "text", "Example Org Ltd"],
                ]],
            },
        ],
    }


@pytest.mark.asyncio
async def test_domain_lookup_normalizes_entities_events_nameservers():
    client, fetch = _client({
        "https://rdap.org/domain/example.com": (200, json.dumps(_rdap_domain_payload())),
    })
    outcome = await client.lookup("example.com", "domain")
    assert outcome["status"] == "observed"
    assert fetch.urls == ["https://rdap.org/domain/example.com"]
    record = outcome["record"]
    assert record["handle"] == "D1234-CNNIC"
    assert record["name"] == "example.com"
    assert record["registrar"] == "Example Registrar Co."
    assert record["abuse_contacts"] == ["abuse@registrar.example"]
    assert record["organizations"] == ["Example Org Ltd"]
    assert record["events"] == {
        "registration": "2020-01-01T00:00:00",
        "expiration": "2027-01-01T00:00:00",
        "last_changed": "2026-01-01T00:00:00",
    }
    assert record["nameservers"] == ["ns1.example.com"]
    assert record["statuses"] == ["client transfer prohibited"]
    assert outcome["source_protocol"] == "rdap"


@pytest.mark.asyncio
async def test_ip_lookup_normalizes_cidrs_and_range():
    payload = {
        "handle": "NET-203-0-113-0-1",
        "name": "EXAMPLE-NET",
        "startAddress": "203.0.113.0",
        "endAddress": "203.0.113.255",
        "ipVersion": "v4",
        "country": "US",
        "cidr0_cidrs": [{"v4": "203.0.113", "value": 24}],
        "status": ["active"],
    }
    client, fetch = _client({
        "https://rdap.org/ip/203.0.113.5": (200, json.dumps(payload)),
    })
    outcome = await client.lookup("203.0.113.5", "ip")
    assert outcome["status"] == "observed"
    assert fetch.urls == ["https://rdap.org/ip/203.0.113.5"]
    record = outcome["record"]
    assert record["cidrs"] == ["203.0.113/24"]
    assert record["start_address"] == "203.0.113.0"
    assert record["end_address"] == "203.0.113.255"
    assert record["ip_version"] == "v4"
    assert record["country"] == "US"


@pytest.mark.asyncio
async def test_asn_lookup_normalizes_autnum_range():
    payload = {"handle": "AS-64512", "name": "EXAMPLE-AS", "startAutnum": 64512, "endAutnum": 64512,
               "country": "DE", "status": ["active"]}
    client, _ = _client({
        "https://rdap.org/autnum/64512": (200, json.dumps(payload)),
    })
    outcome = await client.lookup("64512", "asn")
    assert outcome["status"] == "observed"
    assert outcome["record"]["asn_start"] == 64512
    assert outcome["record"]["asn_end"] == 64512


@pytest.mark.asyncio
async def test_http_404_maps_to_not_found():
    client, _ = _client({
        "https://rdap.org/domain/none.example": (
            404, urllib.error.HTTPError("url", 404, "not found", None, None),
        ),
    })
    outcome = await client.lookup("none.example", "domain")
    assert outcome["status"] == "not_found"
    assert outcome["source_protocol"] == "rdap"


@pytest.mark.asyncio
async def test_http_429_maps_to_rate_limited():
    client, _ = _client({
        "https://rdap.org/ip/198.51.100.1": (
            429, urllib.error.HTTPError("url", 429, "slow down", None, None),
        ),
    })
    outcome = await client.lookup("198.51.100.1", "ip")
    assert outcome["status"] == "rate_limited"


@pytest.mark.asyncio
async def test_other_http_errors_map_to_http_error():
    client, _ = _client({
        "https://rdap.org/domain/example.com": (
            502, urllib.error.HTTPError("url", 502, "bad gateway", None, None),
        ),
    })
    outcome = await client.lookup("example.com", "domain")
    assert outcome["status"] == "http_error"


@pytest.mark.asyncio
async def test_malformed_body_maps_to_invalid_response():
    client, _ = _client({"https://rdap.org/domain/example.com": (200, "<html>err</html>")})
    outcome = await client.lookup("example.com", "domain")
    assert outcome["status"] == "invalid_response"


@pytest.mark.asyncio
async def test_timeout_maps_to_timeout_status():
    class TimeoutFetch:
        def __call__(self, url, timeout):
            raise TimeoutError

    client = RdapOrgClient(fetch=TimeoutFetch())
    outcome = await client.lookup("example.com", "domain")
    assert outcome["status"] == "timeout"


@pytest.mark.asyncio
async def test_query_special_characters_are_percent_encoded():
    client, fetch = _client({})
    await client.lookup("a&b.example", "domain")
    assert fetch.urls == ["https://rdap.org/domain/a%26b.example"]


def test_normalize_rdap_record_ignores_malformed_entities_and_events():
    record = normalize_rdap_record({
        "ldhName": "x.example",
        "entities": ["garbage", {"roles": ["registrar"]}, None],
        "events": ["oops", {"eventAction": "registration", "eventDate": "2020-01-01"}],
        "cidr0_cidrs": [None, {"v4": "10.0", "value": 8}, {"bogus": 1}],
        "status": [1, "active"],
        "nameservers": [{"no_ldh": 1}, {"ldhName": "NS2.X.example."}],
    })
    assert record["registrar"] is None  # entity without vcard fn is ignored
    assert record["events"]["registration"] == "2020-01-01"
    assert record["cidrs"] == ["10.0/8"]
    assert record["statuses"] == ["1", "active"]
    assert record["nameservers"] == ["ns2.x.example"]


def _fake_whois_query_factory(server_responses):
    calls = []

    def query(server, target, timeout):
        calls.append((server, target))
        outcome = server_responses.get(server)
        if outcome is None:
            return "No match for domain."
        referred, text = outcome
        if referred and text is None:
            # emulate the real client following the referral itself
            return query(referred, target, timeout)
        return text

    query.calls = calls
    return query


@pytest.mark.asyncio
async def test_whois_fallback_follows_referral_and_extracts_allowlist():
    raw_text = (
        "Domain Name: EXAMPLE.COM\r\n"
        "Registrar: Fallback Registrar Inc\r\n"
        "Registrant Organization: Fallback Org Ltd\r\n"
        "Creation Date: 2019-05-05T00:00:00Z\r\n"
        "Registry Expiry Date: 2028-05-05T00:00:00Z\r\n"
        "Name Server: NS1.EXAMPLE.COM\r\n"
        "Name Server: NS2.EXAMPLE.COM\r\n"
        "Registrant Email: hidden@privacy.example\r\n"
    )
    query = _fake_whois_query_factory({
        "whois.iana.org": ("whois.verisign-grs.com", None),
        "whois.verisign-grs.com": (None, raw_text),
    })
    client = WhoisFallbackClient(query_func=query)
    outcome = await client.lookup("example.com", "domain")
    assert query.calls == [("whois.iana.org", "example.com"), ("whois.verisign-grs.com", "example.com")]
    assert outcome["status"] == "observed"
    assert outcome["source_protocol"] == "whois"
    record = outcome["record"]
    assert record["name"] == "EXAMPLE.COM"
    assert record["registrar"] == "Fallback Registrar Inc"
    assert record["organizations"] == ["Fallback Org Ltd"]
    assert record["events"]["registration"] == "2019-05-05T00:00:00Z"
    assert record["events"]["expiration"] == "2028-05-05T00:00:00Z"
    assert record["nameservers"] == ["ns1.example.com", "ns2.example.com"]
    # contact addresses are never mined from WHOIS text
    assert record["abuse_contacts"] == []
    assert "hidden@privacy.example" not in json.dumps(record)


@pytest.mark.asyncio
async def test_whois_fallback_reports_not_found_without_referral():
    query = _fake_whois_query_factory({"whois.iana.org": (None, "No match for \"GONE.EXAMPLE\".")})
    client = WhoisFallbackClient(query_func=query)
    outcome = await client.lookup("gone.example", "domain")
    assert outcome["status"] == "not_found"
    assert query.calls == [("whois.iana.org", "gone.example")]


@pytest.mark.asyncio
async def test_whois_fallback_rejects_non_domain_kinds():
    query = _fake_whois_query_factory({})
    client = WhoisFallbackClient(query_func=query)
    outcome = await client.lookup("203.0.113.5", "ip")
    assert outcome["status"] == "unsupported"
    assert query.calls == []


@pytest.mark.asyncio
async def test_sequential_client_falls_back_only_when_rdap_404_and_whois_observes():
    rdap, _ = _client({
        "https://rdap.org/domain/example.com": (
            404, urllib.error.HTTPError("url", 404, "not found", None, None),
        ),
    })
    raw_text = "Domain Name: EXAMPLE.COM\r\nRegistrar: Fallback Registrar Inc\r\n"
    query = _fake_whois_query_factory({
        "whois.iana.org": ("whois.example-registrar", None),
        "whois.example-registrar": (None, raw_text),
    })
    client = SequentialLookupClient(rdap, WhoisFallbackClient(query_func=query))
    outcome = await client.lookup("example.com", "domain")
    assert outcome["status"] == "observed"
    assert outcome["source_protocol"] == "whois"
    assert outcome["record"]["data_quality"] == "whois_fallback"

    # rdap success short-circuits: whois is never asked
    rdap_ok, fetch_ok = _client({
        "https://rdap.org/domain/ok.example": (200, json.dumps(_rdap_domain_payload())),
    })
    query2 = _fake_whois_query_factory({})
    client2 = SequentialLookupClient(rdap_ok, WhoisFallbackClient(query_func=query2))
    outcome2 = await client2.lookup("ok.example", "domain")
    assert outcome2["source_protocol"] == "rdap"
    assert query2.calls == []

    # rdap 404 + whois not_found keeps the rdap 404
    rdap_404, _ = _client({
        "https://rdap.org/domain/gone.example": (
            404, urllib.error.HTTPError("url", 404, "not found", None, None),
        ),
    })
    query3 = _fake_whois_query_factory({})  # "No match" default
    client3 = SequentialLookupClient(rdap_404, WhoisFallbackClient(query_func=query3))
    outcome3 = await client3.lookup("gone.example", "domain")
    assert outcome3["status"] == "not_found"
    assert outcome3["source_protocol"] == "rdap"
