import json

import urllib.error

import pytest

from surface_intel.ct_client import CrtShClient, normalize_ct_row


class FakeHttp:
    """Injectable urllib stand-in: maps URL -> (status, body) or raises."""

    def __init__(self, responses):
        self.responses = responses
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        outcome = self.responses.get(url)
        if outcome is None:
            return 200, "[]"
        status, body = outcome
        if isinstance(body, Exception):
            raise body
        return status, body


def _client(responses):
    fetch = FakeHttp(responses)
    return CrtShClient(endpoint="https://crt.sh/", timeout=5.0, fetch=fetch), fetch


def _crtsh_row(**overrides):
    row = {
        "issuer_ca_id": 164,
        "issuer_name": "C=US, O=Let's Encrypt, CN=E5",
        "common_name": "www.example.com",
        "name_value": "www.example.com\nexample.com",
        "id": 1234567890,
        "entry_timestamp": "2026-01-02T03:04:05.678",
        "not_before": "2026-01-01T00:00:00",
        "not_after": "2026-04-01T00:00:00",
        "serial_number": "04aabbcc",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_crtsh_rows_normalize_into_certificate_shape():
    client, fetch = _client({
        "https://crt.sh/?q=example.com&output=json": (
            200,
            json.dumps([_crtsh_row(), _crtsh_row(id=999)]),
        ),
    })
    outcome = await client.search("example.com")
    assert outcome["status"] == "observed"
    assert len(outcome["certificates"]) == 2
    assert fetch.urls == ["https://crt.sh/?q=example.com&output=json"]
    cert = outcome["certificates"][0]
    assert cert["common_name"] == "www.example.com"
    assert cert["sans"] == ["www.example.com", "example.com"]
    assert cert["issuer"] == "C=US, O=Let's Encrypt, CN=E5"
    assert cert["serial"] == "04aabbcc"
    assert cert["not_before"] == "2026-01-01T00:00:00"
    assert cert["not_after"] == "2026-04-01T00:00:00"
    assert cert["first_seen"] == "2026-01-02T03:04:05.678"
    assert cert["last_seen"] == "2026-01-02T03:04:05.678"
    assert cert["log_id"] == 1234567890
    assert cert["log_source"] == "crt.sh"
    assert outcome["query_ms"] is not None
    assert outcome["queried_at"]


@pytest.mark.asyncio
async def test_search_normalizes_target_and_quotes_query():
    client, fetch = _client({})
    await client.search("Example.COM.")
    assert fetch.urls == ["https://crt.sh/?q=example.com&output=json"]


@pytest.mark.asyncio
async def test_special_characters_are_percent_encoded():
    client, fetch = _client({})
    await client.search("a&b.com")
    # urlencode escapes the ampersand so the query cannot inject extra params
    assert fetch.urls == ["https://crt.sh/?q=a%26b.com&output=json"]


@pytest.mark.asyncio
async def test_empty_result_set_is_definitive_empty():
    client, _ = _client({"https://crt.sh/?q=none.example.com&output=json": (200, "[]")})
    outcome = await client.search("none.example.com")
    assert outcome["status"] == "empty"
    assert outcome["certificates"] == []


@pytest.mark.asyncio
async def test_http_429_maps_to_rate_limited():
    client, _ = _client({
        "https://crt.sh/?q=example.com&output=json": (
            429,
            urllib.error.HTTPError("url", 429, "slow down", None, None),
        ),
    })
    outcome = await client.search("example.com")
    assert outcome["status"] == "rate_limited"
    assert outcome["certificates"] == []


@pytest.mark.asyncio
async def test_other_http_errors_map_to_http_error_status():
    client, _ = _client({
        "https://crt.sh/?q=example.com&output=json": (
            503,
            urllib.error.HTTPError("url", 503, "unavailable", None, None),
        ),
    })
    outcome = await client.search("example.com")
    assert outcome["status"] == "http_error"


@pytest.mark.asyncio
async def test_malformed_body_maps_to_invalid_response():
    client, _ = _client({"https://crt.sh/?q=example.com&output=json": (200, "<html>gateway</html>")})
    outcome = await client.search("example.com")
    assert outcome["status"] == "invalid_response"


@pytest.mark.asyncio
async def test_non_list_json_maps_to_invalid_response():
    client, _ = _client({"https://crt.sh/?q=example.com&output=json": (200, '{"error": "too big"}')})
    outcome = await client.search("example.com")
    assert outcome["status"] == "invalid_response"


@pytest.mark.asyncio
async def test_timeout_error_maps_to_timeout_status():
    class TimeoutFetch:
        def __call__(self, url, timeout):
            raise TimeoutError

    client = CrtShClient(fetch=TimeoutFetch())
    outcome = await client.search("example.com")
    assert outcome["status"] == "timeout"


@pytest.mark.asyncio
async def test_health_reports_configuration_without_network():
    client, fetch = _client({})
    outcome = await client.health()
    assert outcome["ok"] is True
    assert outcome["endpoint"] == "https://crt.sh/"
    assert fetch.urls == []  # health never touches the network


def test_normalize_ct_row_handles_wildcard_and_missing_fields():
    row = normalize_ct_row({
        "common_name": "*.example.com",
        "name_value": "*.example.com\nmail.example.com\n",
        "issuer_name": "issuer",
        "serial_number": "ff",
        "entry_timestamp": "2026-02-02T00:00:00",
    })
    assert row["common_name"] == "*.example.com"
    assert row["sans"] == ["*.example.com", "mail.example.com"]
    assert row["first_seen"] == row["last_seen"] == "2026-02-02T00:00:00"


def test_normalize_ct_row_rejects_non_mapping():
    assert normalize_ct_row("garbage") is None
    assert normalize_ct_row(None) is None
