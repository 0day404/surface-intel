"""D-3: plaintext ports capture the greeting banner; an open port that stays
silent reports open_no_banner - "TCP up, service mute" as its own status."""
import pytest

from surface_intel.safe_port_client import SafePortProbeClient


class _FakeSock:
    def close(self):
        pass


@pytest.mark.asyncio
async def test_plain_port_banner_is_recorded():
    def connect(host, port, timeout):
        return _FakeSock(), None, "8.0.36 MySQL"

    outcome = await SafePortProbeClient(connect=connect).probe("db.example.com", 3306)
    assert outcome["status"] == "open"
    assert outcome["banner"] == "8.0.36 MySQL"
    assert outcome["service_hint"] == "mysql"
    assert outcome["tls_sha256"] is None


@pytest.mark.asyncio
async def test_open_but_silent_port_is_open_no_banner():
    def connect(host, port, timeout):
        return _FakeSock(), None, None  # recv timed out: no greeting came

    outcome = await SafePortProbeClient(connect=connect).probe("www.example.com", 8601)
    assert outcome["status"] == "open_no_banner"  # the 8601 shape
    assert outcome["banner"] is None


@pytest.mark.asyncio
async def test_tls_port_with_cert_stays_open_not_no_banner():
    def connect(host, port, timeout):
        return _FakeSock(), b"der-cert", None  # TLS path never reads a banner

    outcome = await SafePortProbeClient(connect=connect).probe("www.example.com", 443)
    assert outcome["status"] == "open"
    assert outcome["tls_sha256"] is not None
