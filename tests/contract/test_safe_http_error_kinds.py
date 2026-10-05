"""P03: timeout classification carries its phase (connect vs app_layer) and
TLS failures distinguish certificate verification from handshake problems."""
import ssl

import pytest

from surface_intel.safe_http_client import FetchTimeout, SafeHttpFetchClient


def _fail(exc):
    def fetch(url, method, timeout):
        raise exc
    return fetch


@pytest.mark.asyncio
async def test_timeout_error_types_carry_the_phase():
    connect = await SafeHttpFetchClient(fetch_hop=_fail(FetchTimeout("connect"))).observe(
        "https://www.example.com/", allowed_hosts=("example.com",))
    assert connect["status"] == "timeout"
    assert connect["error_type"] == "timeout_connect"

    app_layer = await SafeHttpFetchClient(fetch_hop=_fail(FetchTimeout("app_layer"))).observe(
        "https://www.example.com/", allowed_hosts=("example.com",))
    assert app_layer["status"] == "timeout"
    assert app_layer["error_type"] == "timeout_app_layer"


@pytest.mark.asyncio
async def test_bare_timeout_from_injected_hops_is_connect_by_conservative_default():
    outcome = await SafeHttpFetchClient(fetch_hop=_fail(TimeoutError())).observe(
        "https://www.example.com/", allowed_hosts=("example.com",))
    assert outcome["error_type"] == "timeout_connect"


@pytest.mark.asyncio
async def test_tls_error_records_failure_kind_and_plain_http_hint():
    cert = await SafeHttpFetchClient(
        fetch_hop=_fail(ssl.SSLCertVerificationError("certificate verify failed"))).observe(
        "https://www.example.com/", allowed_hosts=("example.com",))
    assert cert["status"] == "tls_error"
    assert cert["tls_failure_kind"] == "cert_verify"
    # R3 lesson: TLS-refusing hosts often answer on plain http - say so
    assert cert["hint"] == "plain_http_may_be_available"

    handshake = await SafeHttpFetchClient(fetch_hop=_fail(ssl.SSLError("no shared cipher"))).observe(
        "https://www.example.com/", allowed_hosts=("example.com",))
    assert handshake["status"] == "tls_error"
    assert handshake["tls_failure_kind"] == "handshake"
    assert handshake["hint"] == "plain_http_may_be_available"
