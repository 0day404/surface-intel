"""R3.4 multi-source unified-result matrix.

Cross-source contract tests over the three passive adapters (DNS, CT, RDAP):
one shared observation metadata contract, conflict coexistence, definitive
negatives, infrastructure failures, license/source unavailability, and
redaction of sensitive action fields in the audit trail.
"""

import json

import pytest
from dataclasses import replace

from surface_intel.adapters.base import TaskContext
from surface_intel.adapters.capabilities import CapabilityRegistry
from surface_intel.adapters.ct import CertificateTransparencyAdapter
from surface_intel.adapters.dns import DnsAdapter
from surface_intel.adapters.rdap import RdapAdapter
from surface_intel.ct_runner import run_ct_search
from surface_intel.dns_runner import run_dns_lookup
from surface_intel.execution import AdapterExecutor, ExecutionContext
from surface_intel.policy.scope import Scope
from surface_intel.rdap_runner import run_rdap_lookup
from surface_intel.state.budget import BudgetRepository
from surface_intel.state.database import Database
from surface_intel.state.observations import UNIFIED_METADATA_KEYS


class StubDnsClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def resolve(self, name, record_type):
        self.calls.append((name, record_type))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return dict(self.outcome)


class StubCtClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def search(self, domain):
        self.calls.append(domain)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return dict(self.outcome)


class StubRdapClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def lookup(self, target, kind):
        self.calls.append((target, kind))
        if isinstance(self.outcome, Exception):
            raise self.outcome
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


def _scope(**overrides):
    defaults = dict(
        authorized_domains=("example.com", "*.example.com"),
        authorized_ips=("203.0.113.5",),
        confirmed=True,
        scope_version="scope-v1",
    )
    defaults.update(overrides)
    return Scope(**defaults)


def _payloads(db):
    rows = list(db.rows("SELECT payload_json,source FROM observation ORDER BY rowid"))
    return [(row["source"], json.loads(row["payload_json"])) for row in rows]


@pytest.mark.asyncio
async def test_unified_metadata_contract_across_all_three_sources(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    dns = StubDnsClient({"status": "observed", "records": ["203.0.113.5"], "ttl": 300})
    ct = StubCtClient({"status": "observed", "certificates": [{
        "common_name": "www.example.com", "sans": ["www.example.com"],
        "issuer": "issuer", "serial": "aa", "not_before": "n", "not_after": "x",
        "first_seen": "f", "last_seen": "l", "log_id": 1, "log_source": "crt.sh",
    }]})
    rdap = StubRdapClient({"status": "observed", "source_protocol": "rdap", "record": {
        "handle": "NET-X", "name": "EXAMPLE-NET", "cidrs": ["203.0.113/24"],
    }})
    assert (await run_dns_lookup(db, dns, task_id="t", scope=scope, domain="www.example.com", record_types=["A"])).status == "completed"
    assert (await run_ct_search(db, ct, task_id="t", scope=scope, domain="example.com")).status == "completed"
    assert (await run_rdap_lookup(db, rdap, task_id="t", scope=scope, target="203.0.113.5", kind="ip")).status == "completed"

    rows = _payloads(db)
    assert {source for source, _ in rows} == {"dns_resolver", "ct_log", "rdap_lookup"}
    for source, payload in rows:
        for key in UNIFIED_METADATA_KEYS:
            assert payload.get(key) is not None, (source, key)
        assert payload["scope_version"] == "scope-v1"
        assert payload["source_quality"] in {"direct", "whois_fallback"}
        assert payload["license_status"] in {
            "public_protocol", "public_ct_log", "public_rdap", "whois_text_unverified",
        }
        assert payload["related_scope_status"] in {
            "confirmed", "candidate", "needs_review", "not_applicable",
        }
    evidence = list(db.rows("SELECT source,redaction_status,sha256 FROM evidence"))
    assert len(evidence) == 3
    assert all(item["redaction_status"] == "redacted" for item in evidence)


@pytest.mark.asyncio
async def test_conflicting_observations_coexist_without_overwrite_or_confirmation(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    # DNS says the authorized name points at 203.0.113.5...
    dns = StubDnsClient({"status": "observed", "records": ["203.0.113.5"], "ttl": 300})
    await run_dns_lookup(db, dns, task_id="t", scope=scope, domain="www.example.com", record_types=["A"])
    # ...CT shows a cert also naming a third party...
    ct = StubCtClient({"status": "observed", "certificates": [{
        "common_name": "www.example.com", "sans": ["www.example.com", "partner.thirdparty.net"],
        "issuer": "issuer", "serial": "bb", "not_before": "n", "not_after": "x",
        "first_seen": "f", "last_seen": "l", "log_id": 2, "log_source": "crt.sh",
    }]})
    await run_ct_search(db, ct, task_id="t", scope=scope, domain="example.com")
    # ...while RDAP attributes the network to a different organization.
    rdap = StubRdapClient({"status": "observed", "source_protocol": "rdap", "record": {
        "handle": "NET-Y", "name": "OTHER-HOSTING", "organizations": ["Other Hosting Ltd"],
        "cidrs": ["203.0.113/24"],
    }})
    await run_rdap_lookup(db, rdap, task_id="t", scope=scope, target="203.0.113.5", kind="ip")

    rows = _payloads(db)
    assert len(rows) == 3
    by_source = dict(rows)
    assert by_source["dns_resolver"]["records"] == ["203.0.113.5"]
    assert "partner.thirdparty.net" in by_source["ct_log"]["certificates"][0]["sans"]
    assert by_source["rdap_lookup"]["record"]["organizations"] == ["Other Hosting Ltd"]
    # conflicts are preserved verbatim, never silently resolved...
    assert by_source["ct_log"]["related_scope_status"] == "needs_review"
    # ...CT and RDAP never confirm; DNS may only confirm because 203.0.113.5
    # is itself independently authorized (the R3.1 contract, re-checked here).
    assert by_source["rdap_lookup"]["related_scope_status"] == "candidate"
    assert by_source["dns_resolver"]["related_scope_status"] == "confirmed"
    assert next(db.rows("SELECT COUNT(*) AS count FROM asset"))["count"] == 0


@pytest.mark.asyncio
async def test_definitive_negatives_are_uniform_across_sources(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    dns_result = await run_dns_lookup(db, StubDnsClient({"status": "nxdomain", "records": []}),
                                      task_id="t", scope=scope, domain="gone.example.com")
    ct_result = await run_ct_search(db, StubCtClient({"status": "empty", "certificates": []}),
                                    task_id="t", scope=scope, domain="gone2.example.com")
    rdap_result = await run_rdap_lookup(db, StubRdapClient({"status": "not_found", "source_protocol": "rdap"}),
                                        task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    assert (dns_result.status, ct_result.status, rdap_result.status) == ("completed", "completed", "completed")
    for _, payload in _payloads(db):
        assert payload["status"] in {"nxdomain", "empty", "not_found"}
        assert payload["related_scope_status"] == "not_applicable"


@pytest.mark.asyncio
async def test_timeout_and_rate_limit_failures_are_uniform(tmp_path):
    scope = _scope()

    for name, client in (
        ("dns-timeout", StubDnsClient(TimeoutError())),
        ("dns-ratelimited", StubDnsClient({"status": "rate_limited", "records": []})),
    ):
        db = _setup(tmp_path / name, scope)
        result = await run_dns_lookup(db, client, task_id="t", scope=scope,
                                      domain="www.example.com", record_types=["A"])
        assert result.status == "failed"
        assert result.error in {"timeout", "rate_limited"}
        # infra observations are still persisted for audit
        infra = _payloads(db)
        assert infra[0][1]["status"] in {"timeout", "rate_limited"}

    db = _setup(tmp_path / "ct", scope)
    result = await run_ct_search(db, StubCtClient({"status": "rate_limited", "certificates": []}),
                                 task_id="t", scope=scope, domain="example.com")
    assert result.status == "failed" and result.error == "rate_limited"
    assert _payloads(db)[0][1]["status"] == "rate_limited"

    db = _setup(tmp_path / "rdap", scope)
    result = await run_rdap_lookup(db, StubRdapClient(TimeoutError()),
                                   task_id="t", scope=scope, target="203.0.113.5", kind="ip")
    assert result.status == "failed" and result.error == "timeout"
    assert _payloads(db)[0][1]["status"] == "timeout"


@pytest.mark.asyncio
async def test_source_or_license_unavailable_blocks_before_client_call(tmp_path):
    """An adapter whose capability status is degraded/not_configured never runs."""
    scope = _scope()
    db = _setup(tmp_path, scope)
    BudgetRepository(db).create("t", "dns_resolver", 10)
    client = StubDnsClient({"status": "observed", "records": ["203.0.113.5"]})

    class UnavailableDns(DnsAdapter):
        manifest = replace(DnsAdapter.manifest, capability_status="not_configured")

    registry = CapabilityRegistry([UnavailableDns.manifest])
    executor = AdapterExecutor(db, registry)
    context = ExecutionContext(
        task_id="t", scope_version=scope.scope_version, scope_confirmed=True,
        metadata={"scope_payload": scope.to_dict()},
    )
    result = await executor.execute(
        UnavailableDns(client),
        {"action_id": "dns-x", "target": "www.example.com", "capability": "dns_resolve",
         "record_types": ["A"], "budget_kind": "dns_resolver"},
        context,
    )
    assert result.status == "blocked"
    assert result.error == "adapter_not_configured"
    assert client.calls == []  # unavailable source never touches the network
    assert _payloads(db) == []


@pytest.mark.asyncio
async def test_sensitive_action_fields_are_redacted_in_audit_trail(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    registry = CapabilityRegistry([CertificateTransparencyAdapter.manifest])
    executor = AdapterExecutor(db, registry)
    context = ExecutionContext(
        task_id="t", scope_version=scope.scope_version, scope_confirmed=True,
        metadata={"scope_payload": scope.to_dict()},
    )
    await executor.execute(
        CertificateTransparencyAdapter(StubCtClient({"status": "empty", "certificates": []})),
        {"action_id": "ct-x", "target": "example.com", "capability": "ct_search",
         "api_key": "super-secret-42", "budget_kind": "ct_log"},
        context,
    )
    row = next(db.rows("SELECT input_summary FROM adapter_call"))
    summary = json.loads(row["input_summary"])
    assert summary["api_key"] == "[REDACTED]"  # value never stored in the clear
    assert "[REDACTED]" in row["input_summary"]
