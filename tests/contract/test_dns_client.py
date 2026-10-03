import dns.rrset
import dns.resolver
import pytest

from surface_intel.dns_client import DnspythonResolverClient, configured_dns_client


class StubResolver:
    def __init__(self, outcome=None, exc=None):
        self.outcome = outcome
        self.exc = exc
        self.nameservers = ["10.0.0.1"]

    async def resolve(self, qname, rdtype, **kwargs):
        if self.exc is not None:
            raise self.exc
        return self.outcome


class _FlagsHolder:
    def __init__(self, flags_value):
        self.flags = flags_value


class StubAnswer:
    """Mirrors dns.resolver.Answer: rdata iteration plus .response.flags."""

    def __init__(self, rrset, flags_value=0):
        self.rrset = rrset
        self.response = _FlagsHolder(flags_value)

    def __iter__(self):
        return iter(self.rrset)


def _client(resolver):
    return DnspythonResolverClient(resolver=resolver, timeout=1.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("exc,status", [
    (dns.resolver.NXDOMAIN(), "nxdomain"),
    (dns.resolver.NoAnswer(), "empty"),
    (dns.resolver.NoNameservers(), "servfail"),
    (dns.exception.Timeout(), "timeout"),
])
async def test_resolver_exceptions_map_to_structured_statuses(exc, status):
    outcome = await _client(StubResolver(exc=exc)).resolve("example.com", "A")
    assert outcome["status"] == status
    assert outcome["records"] == []
    assert outcome["name"] == "example.com"


@pytest.mark.asyncio
async def test_a_records_extract_address_ttl_and_ad_bit():
    import dns.flags

    rrset = dns.rrset.from_text("example.com.", 300, "IN", "A", "93.184.216.34")
    answer = StubAnswer(rrset, flags_value=dns.flags.AD)
    outcome = await _client(StubResolver(outcome=answer)).resolve("example.com", "A")
    assert outcome["status"] == "observed"
    assert outcome["records"] == ["93.184.216.34"]
    assert outcome["ttl"] == 300
    assert outcome["dnssec"] is True


@pytest.mark.asyncio
async def test_mx_records_extract_preference_and_exchange():
    rrset = dns.rrset.from_text("example.com.", 600, "IN", "MX", "10 mail.example.com.")
    outcome = await _client(StubResolver(outcome=StubAnswer(rrset))).resolve("example.com", "MX")
    assert outcome["records"] == ["10 mail.example.com"]


@pytest.mark.asyncio
async def test_txt_records_join_string_fragments():
    rrset = dns.rrset.from_text("example.com.", 60, "IN", "TXT", '"v=spf1 -all"')
    outcome = await _client(StubResolver(outcome=StubAnswer(rrset))).resolve("example.com", "TXT")
    assert outcome["records"] == ["v=spf1 -all"]


@pytest.mark.asyncio
async def test_queries_use_absolute_names_to_skip_search_lists():
    seen = {}

    class RecordingResolver(StubResolver):
        async def resolve(self, qname, rdtype, **kwargs):
            seen["qname"] = str(qname)
            seen["rdtype"] = str(rdtype)
            raise dns.resolver.NXDOMAIN()

    await _client(RecordingResolver()).resolve("Example.COM", "A")
    assert seen["qname"] == "example.com."  # absolute, lowercased
    assert seen["rdtype"].upper().endswith("A")


def test_configured_client_uses_env_nameservers(monkeypatch):
    monkeypatch.setenv("SURFACE_INTEL_DNS_NAMESERVERS", "1.1.1.1, 8.8.8.8")
    client = configured_dns_client()
    assert sorted(str(item) for item in client._resolver.nameservers) == ["1.1.1.1", "8.8.8.8"]
