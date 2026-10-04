"""R6.1 Safe HTTP client: manual redirect authorization, private-IP refusal,
digest-only bodies, and distinct status modeling - all over an injected hop."""
import hashlib

import pytest

from surface_intel.safe_http_client import SafeHttpFetchClient


def _hop(status=200, headers=None, body=b"hello", peer=b"cert-der"):
    def fetch(url, method, timeout):
        return status, (headers or {}), body, (peer if url.startswith("https://") else None)
    return fetch


def _fail(exc):
    def fetch(url, method, timeout):
        raise exc
    return fetch


@pytest.mark.asyncio
async def test_observed_hop_records_digest_headers_and_tls():
    body = b"<html>ok</html>"
    client = SafeHttpFetchClient(fetch_hop=_hop(200, {
        "server": "nginx", "set-cookie": "session=secret", "content-type": "text/html",
    }, body))
    outcome = await client.observe("https://www.example.com/", allowed_hosts=("example.com",))
    assert outcome["status"] == "observed"
    assert outcome["http_status"] == 200
    assert outcome["response_class"] == "ok"
    assert outcome["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert outcome["body_bytes"] == len(body)
    assert outcome["body_truncated"] is False
    assert outcome["headers"] == {"server": "nginx", "content-type": "text/html"}
    assert "set-cookie" not in outcome["headers"]  # never persisted
    assert outcome["tls_sha256"] == hashlib.sha256(b"cert-der").hexdigest()
    assert outcome["final_url"] == "https://www.example.com/"


@pytest.mark.asyncio
async def test_in_scope_redirects_are_followed_and_recorded():
    calls = []

    def fetch(url, method, timeout):
        calls.append(url)
        if url == "https://www.example.com/":
            return 301, {"location": "https://www.example.com/login"}, b"", None
        return 200, {"Content-Type": "text/html"}, b"final", None

    client = SafeHttpFetchClient(fetch_hop=fetch)
    outcome = await client.observe("https://www.example.com/", allowed_hosts=("example.com",))
    assert outcome["status"] == "observed"
    assert calls == ["https://www.example.com/", "https://www.example.com/login"]
    assert outcome["final_url"].endswith("/login")
    assert [hop["http_status"] for hop in outcome["redirect_chain"]] == [301, 200]


@pytest.mark.asyncio
async def test_third_party_redirect_is_blocked_not_followed():
    def fetch(url, method, timeout):
        return 302, {"location": "https://tracker.thirdparty.net/pixel"}, b"", None

    client = SafeHttpFetchClient(fetch_hop=fetch)
    outcome = await client.observe("https://www.example.com/", allowed_hosts=("example.com",))
    assert outcome["status"] == "redirect_blocked"
    assert outcome["blocked_host"] == "tracker.thirdparty.net"
    # B4: where the blocked redirect was headed is recorded without contacting it
    assert outcome["blocked_url"] == "https://tracker.thirdparty.net/pixel"
    assert outcome["redirect_chain"][0]["location"] == "https://tracker.thirdparty.net/pixel"
    # the chain records where we stopped, but the third party was never contacted
    assert outcome["redirect_chain"][0]["http_status"] == 302


@pytest.mark.asyncio
async def test_private_addresses_are_refused_before_connecting(tmp_path):
    resolved = []

    def resolve(host):
        resolved.append(host)
        return ["127.0.0.1"]

    fetch_calls = []

    def fetch(url, method, timeout):
        fetch_calls.append(url)
        return 200, {}, b"", None

    client = SafeHttpFetchClient(fetch_hop=fetch, resolver=resolve)
    outcome = await client.observe("https://www.example.com/")
    assert outcome["status"] == "private_blocked"
    assert outcome["blocked_address"] == "127.0.0.1"
    assert fetch_calls == []  # refused before any connection attempt


@pytest.mark.asyncio
async def test_timeout_and_tls_and_connection_errors_are_distinct():
    import socket
    import ssl

    timeout_client = SafeHttpFetchClient(fetch_hop=_fail(TimeoutError()))
    assert (await timeout_client.observe("https://www.example.com/"))["status"] == "timeout"

    tls_client = SafeHttpFetchClient(fetch_hop=_fail(ssl.SSLError("certificate verify failed")))
    tls_outcome = await tls_client.observe("https://www.example.com/")
    assert tls_outcome["status"] == "tls_error"
    # B3: the concrete handshake failure is preserved for remote triage
    assert tls_outcome["error_type"] == "SSLError"
    assert "certificate verify failed" in tls_outcome["error"]

    conn_client = SafeHttpFetchClient(fetch_hop=_fail(ConnectionResetError()))
    outcome = await conn_client.observe("https://www.example.com/")
    assert outcome["status"] == "http_error"
    assert outcome["error_type"] == "ConnectionResetError"


@pytest.mark.asyncio
async def test_response_classes_split_200_404_403_429():
    for status, expected in ((200, "ok"), (404, "not_found"), (403, "forbidden"),
                             (429, "rate_limited"), (500, "other")):
        client = SafeHttpFetchClient(fetch_hop=_hop(status))
        outcome = await client.observe("https://www.example.com/")
        assert outcome["response_class"] == expected, status


@pytest.mark.asyncio
async def test_body_cap_sets_truncated_flag():
    client = SafeHttpFetchClient(fetch_hop=_hop(200, body=b"x" * 40), max_body_bytes=16)
    outcome = await client.observe("https://www.example.com/")
    assert outcome["body_bytes"] == 16
    assert outcome["body_truncated"] is True


@pytest.mark.asyncio
async def test_invalid_url_and_method_are_structural_refusals():
    client = SafeHttpFetchClient(fetch_hop=_hop())
    assert (await client.observe("ftp://www.example.com/"))["status"] == "http_error"
    outcome = await client.observe("https://www.example.com/", method="POST")
    assert outcome["status"] == "http_error"
    assert outcome["error_type"] == "method_not_allowed"


@pytest.mark.asyncio
async def test_wildcard_scope_hosts_authorize_subdomains():
    client = SafeHttpFetchClient(fetch_hop=_hop())
    outcome = await client.observe("https://www.example.com/", allowed_hosts=("*", "example.com"))
    assert outcome["status"] == "observed"
