"""B3: a TLS-likely port that answers but fails the handshake stays open,
with the concrete handshake failure recorded for remote triage."""
import ssl

import pytest

from surface_intel.safe_port_client import SafePortProbeClient


@pytest.mark.asyncio
async def test_tls_handshake_failure_stays_open_with_error_type():
    def connect(host, port, timeout):
        raise ssl.SSLError("certificate verify failed")

    client = SafePortProbeClient(connect=connect)
    outcome = await client.probe("www.example.com", 443)
    assert outcome["status"] == "open"
    assert outcome["tls_error"] is True
    assert outcome["error_type"] == "SSLError"
