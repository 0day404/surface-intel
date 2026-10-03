import json

import urllib.error

import pytest

from surface_intel.ct_client import (
    CrtlogsDevClient,
    configured_ct_client,
    apply_ctlogs_detail,
    normalize_ctlogs_row,
)
from surface_intel.ct_runner import run_ct_search
from surface_intel.policy.scope import Scope
from surface_intel.state.database import Database


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


SEARCH_URL = "https://ctlogs.dev/search?q=example.com&output=json"
DETAIL_BASE = "https://api.ctlogs.dev/v1/cert"


def _search_body(rows):
    return json.dumps({"rows": rows})


def _search_row(**overrides):
    row = {
        "id": "000000b501eee6aabb521d5e14fc315fd9985690",
        "match": "example.com",
        "not_before": "2026-09-26T22:49:11Z",
        "not_after": "2026-12-25T22:56:35Z",
        "serial_hex": "01eee6aabb521d5e14fc315fd9985690",
        "issuer": "SSL Corporation",
        "key_algo": "ECDSA P-256",
        "san_count": 2,
    }
    row.update(overrides)
    return row


def _detail_payload():
    return {
        "id": "000000b501eee6aabb521d5e14fc315fd9985690",
        "subject_cn": "example.com",
        "issuer_dn": "CN=Cloudflare TLS Issuing ECC CA 3,O=SSL Corporation,C=US",
        "san_dns": ["WWW.Example.com", "example.com"],
        "serial_hex": "01eee6aabb521d5e14fc315fd9985690",
        "not_before": "2026-09-26T22:49:11Z",
        "not_after": "2026-12-25T22:56:35Z",
        "sightings": [
            {"sct_ts": "2026-09-26T22:59:16.306Z", "operator": "DigiCert"},
            {"sct_ts": "2026-09-26T22:59:12.866Z", "operator": "Google"},
        ],
    }


def _client(responses, **kwargs):
    fetch = FakeHttp(responses)
    kwargs.setdefault("fetch", fetch)
    return CrtlogsDevClient(**kwargs), fetch


@pytest.mark.asyncio
async def test_search_rows_map_to_match_only_certificate_shape():
    client, fetch = _client({SEARCH_URL: (200, _search_body([_search_row(), _search_row(id="ff00")]))})
    outcome = await client.search("example.com")
    assert outcome["status"] == "observed"
    assert outcome["log_source"] == "ctlogs.dev"
    assert len(outcome["certificates"]) == 2
    cert = outcome["certificates"][0]
    assert cert["sans"] == ["example.com"]  # match only - no SAN list in search rows
    assert cert["common_name"] is None
    assert cert["issuer"] == "SSL Corporation"  # issuer CN, not full DN
    assert cert["serial"] == "01eee6aabb521d5e14fc315fd9985690"
    assert cert["not_before"] == "2026-09-26T22:49:11Z"
    assert cert["not_after"] == "2026-12-25T22:56:35Z"
    assert cert["first_seen"] is None and cert["last_seen"] is None
    assert cert["log_id"] == "000000b501eee6aabb521d5e14fc315fd9985690"
    assert cert["log_source"] == "ctlogs.dev"
    assert cert["san_count"] == 2
    assert cert["san_completeness"] == "match_only"


@pytest.mark.asyncio
async def test_detail_backfill_fills_sans_cn_dn_and_first_last_seen():
    detail_url = f"{DETAIL_BASE}/000000b501eee6aabb521d5e14fc315fd9985690"
    client, fetch = _client({
        SEARCH_URL: (200, _search_body([_search_row()])),
        detail_url: (200, json.dumps(_detail_payload())),
    })
    outcome = await client.search("example.com")
    assert fetch.urls == [SEARCH_URL, detail_url]
    cert = outcome["certificates"][0]
    assert cert["san_completeness"] == "full"
    assert cert["sans"] == ["www.example.com", "example.com"]  # normalized casing
    assert cert["common_name"] == "example.com"
    assert cert["issuer"] == "CN=Cloudflare TLS Issuing ECC CA 3,O=SSL Corporation,C=US"
    assert cert["first_seen"] == "2026-09-26T22:59:12.866Z"  # min sighting
    assert cert["last_seen"] == "2026-09-26T22:59:16.306Z"  # max sighting


@pytest.mark.asyncio
async def test_detail_backfill_respects_limit():
    rows = [_search_row(id=f"{i:064x}") for i in range(4)]
    responses = {SEARCH_URL: (200, _search_body(rows))}
    for i in range(4):
        responses[f"{DETAIL_BASE}/{i:064x}"] = (200, json.dumps(_detail_payload()))
    client, fetch = _client(responses, detail_limit=2)
    outcome = await client.search("example.com")
    assert len([url for url in fetch.urls if url.startswith(DETAIL_BASE)]) == 2
    assert outcome["certificates"][0]["san_completeness"] == "full"
    assert outcome["certificates"][2]["san_completeness"] == "match_only"


@pytest.mark.asyncio
async def test_detail_failure_degrades_to_match_only_row():
    detail_url = f"{DETAIL_BASE}/000000b501eee6aabb521d5e14fc315fd9985690"
    client, _ = _client({
        SEARCH_URL: (200, _search_body([_search_row()])),
        detail_url: (404, urllib.error.HTTPError("url", 404, "gone", None, None)),
    })
    outcome = await client.search("example.com")
    assert outcome["status"] == "observed"  # search result survives detail failure
    cert = outcome["certificates"][0]
    assert cert["san_completeness"] == "match_only"
    assert cert["sans"] == ["example.com"]


@pytest.mark.asyncio
async def test_detail_limit_zero_skips_backfill():
    client, fetch = _client({SEARCH_URL: (200, _search_body([_search_row()]))}, detail_limit=0)
    outcome = await client.search("example.com")
    assert fetch.urls == [SEARCH_URL]
    assert outcome["certificates"][0]["san_completeness"] == "match_only"


@pytest.mark.asyncio
async def test_empty_rows_is_definitive_empty():
    client, _ = _client({SEARCH_URL: (200, _search_body([]))})
    outcome = await client.search("example.com")
    assert outcome["status"] == "empty"
    assert outcome["certificates"] == []


@pytest.mark.asyncio
async def test_envelope_without_rows_maps_to_invalid_response():
    client, _ = _client({SEARCH_URL: (200, json.dumps({"error": "boom"}))})
    outcome = await client.search("example.com")
    assert outcome["status"] == "invalid_response"


@pytest.mark.asyncio
async def test_malformed_body_maps_to_invalid_response():
    client, _ = _client({SEARCH_URL: (200, "<html>busy</html>")})
    outcome = await client.search("example.com")
    assert outcome["status"] == "invalid_response"


@pytest.mark.asyncio
async def test_http_429_maps_to_rate_limited():
    client, _ = _client({
        SEARCH_URL: (429, urllib.error.HTTPError("url", 429, "slow down", None, None)),
    })
    outcome = await client.search("example.com")
    assert outcome["status"] == "rate_limited"


@pytest.mark.asyncio
async def test_other_http_errors_map_to_http_error():
    client, _ = _client({
        SEARCH_URL: (503, urllib.error.HTTPError("url", 503, "busy", None, None)),
    })
    outcome = await client.search("example.com")
    assert outcome["status"] == "http_error"


@pytest.mark.asyncio
async def test_timeout_maps_to_timeout_status():
    class TimeoutFetch:
        def __call__(self, url, timeout):
            raise TimeoutError

    client = CrtlogsDevClient(fetch=TimeoutFetch())
    outcome = await client.search("example.com")
    assert outcome["status"] == "timeout"


@pytest.mark.asyncio
async def test_query_special_characters_are_percent_encoded():
    client, fetch = _client({})
    await client.search("a&b.com")
    assert fetch.urls == ["https://ctlogs.dev/search?q=a%26b.com&output=json"]


@pytest.mark.asyncio
async def test_health_reports_configuration_without_network():
    client, fetch = _client({}, api_key=None)
    outcome = await client.health()
    assert outcome["ok"] is True
    assert outcome["endpoint"] == "https://ctlogs.dev/search"
    assert outcome["detail_endpoint"] == DETAIL_BASE
    assert outcome["detail_limit"] == 5
    assert outcome["api_key_configured"] is False
    assert outcome["source"] == "ctlogs.dev"


def test_headers_include_bearer_only_when_key_set():
    assert CrtlogsDevClient(api_key=None)._headers() == {"Accept": "application/json"}
    headers = CrtlogsDevClient(api_key="key-abc")._headers()
    assert headers["Authorization"] == "Bearer key-abc"


def test_normalize_ctlogs_row_rejects_non_mapping():
    assert normalize_ctlogs_row("garbage") is None
    assert normalize_ctlogs_row(None) is None


def test_apply_ctlogs_detail_ignores_malformed_sections():
    row = {"sans": ["example.com"], "san_completeness": "match_only"}
    updated = apply_ctlogs_detail(row, {
        "san_dns": "not-a-list",
        "sightings": "nope",
        "subject_cn": "",
    })
    assert updated["san_completeness"] == "match_only"
    assert updated["sans"] == ["example.com"]


def test_configured_ct_client_switches_source_via_env(monkeypatch):
    monkeypatch.delenv("SURFACE_INTEL_CT_SOURCE", raising=False)
    from surface_intel.ct_client import CrtShClient, FailoverCtClient

    # default is now failover (crt.sh primary, ctlogs.dev fallback)
    assert isinstance(configured_ct_client(), FailoverCtClient)

    monkeypatch.setenv("SURFACE_INTEL_CT_SOURCE", "ctlogs")
    client = configured_ct_client()
    assert isinstance(client, CrtlogsDevClient)
    assert client._api_key is None and client._detail_limit == 5

    monkeypatch.setenv("SURFACE_INTEL_CT_API_KEY", "key-abc")
    monkeypatch.setenv("SURFACE_INTEL_CT_DETAIL_LIMIT", "9")
    client = configured_ct_client()
    assert client._api_key == "key-abc" and client._detail_limit == 9

    with pytest.raises(ValueError, match="unknown_ct_source"):
        monkeypatch.setenv("SURFACE_INTEL_CT_SOURCE", "yahoo")
        configured_ct_client()

    with pytest.raises(ValueError, match="invalid_ct_detail_limit"):
        monkeypatch.setenv("SURFACE_INTEL_CT_SOURCE", "ctlogs")
        monkeypatch.setenv("SURFACE_INTEL_CT_DETAIL_LIMIT", "many")
        configured_ct_client()


class FixtureCtClient:
    def __init__(self, outcome):
        self.outcome = outcome

    async def search(self, domain):
        return dict(self.outcome)


def _setup(tmp_path, scope):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0',?)",
            (scope.scope_version,),
        )
    return db


@pytest.mark.asyncio
async def test_ctlogs_shaped_outcome_flows_through_runner(tmp_path):
    scope = Scope(authorized_domains=("example.com", "*.example.com"), confirmed=True,
                  scope_version="scope-v1")
    db = _setup(tmp_path, scope)
    client = FixtureCtClient({
        "status": "observed",
        "log_source": "ctlogs.dev",
        "certificates": [normalize_ctlogs_row(_search_row())],
    })
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    assert result.status == "completed"
    rows = list(db.rows("SELECT payload_json FROM observation"))
    payload = json.loads(rows[0]["payload_json"])
    cert = payload["certificates"][0]
    assert cert["log_source"] == "ctlogs.dev"
    assert cert["san_completeness"] == "match_only"
    # the matched name is in scope, so the observation stays candidate
    assert payload["related_scope_status"] == "candidate"


@pytest.mark.asyncio
async def test_out_of_scope_match_still_escalates_to_needs_review(tmp_path):
    scope = Scope(authorized_domains=("example.com",), confirmed=True, scope_version="scope-v1")
    db = _setup(tmp_path, scope)
    client = FixtureCtClient({
        "status": "observed",
        "log_source": "ctlogs.dev",
        "certificates": [normalize_ctlogs_row(_search_row(match="notexample.org"))],
    })
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    assert result.status == "completed"
    payload = json.loads(list(db.rows("SELECT payload_json FROM observation"))[0]["payload_json"])
    # contains-noise matches on third-party names keep the same escalation as crt.sh
    assert payload["related_scope_status"] == "needs_review"
