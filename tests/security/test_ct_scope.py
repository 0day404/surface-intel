import json

import pytest

from surface_intel.ct_runner import run_ct_search
from surface_intel.policy.scope import Scope
from surface_intel.state.database import Database


class SpyCtClient:
    """Records every search; must never be reached when gates refuse."""

    def __init__(self, certificates):
        self.certificates = certificates
        self.calls = []

    async def search(self, domain):
        self.calls.append(domain)
        return {"status": "observed", "certificates": self.certificates}


def _cert(**overrides):
    row = {
        "common_name": "www.example.com",
        "sans": ["www.example.com", "example.com"],
        "issuer": "C=US, O=Let's Encrypt, CN=E5",
        "serial": "04aabbcc",
        "not_before": "2026-01-01T00:00:00",
        "not_after": "2026-04-01T00:00:00",
        "first_seen": "2026-01-02T00:00:00.000",
        "last_seen": "2026-01-02T00:00:00.000",
        "log_id": 1234567890,
        "log_source": "crt.sh",
    }
    row.update(overrides)
    return row


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
        confirmed=True,
        scope_version="scope-v1",
    )
    defaults.update(overrides)
    return Scope(**defaults)


def _payload(db):
    rows = list(db.rows("SELECT payload_json FROM observation ORDER BY observation_id"))
    return [json.loads(row["payload_json"]) for row in rows]


@pytest.mark.asyncio
async def test_unconfirmed_scope_blocks_before_client_call(tmp_path):
    scope = _scope(confirmed=False)
    db = _setup(tmp_path, scope)
    client = SpyCtClient([_cert()])
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    assert result.status == "blocked"
    assert result.error == "scope_not_confirmed"
    assert client.calls == []
    assert list(db.rows("SELECT 1 FROM observation")) == []


@pytest.mark.asyncio
async def test_out_of_scope_target_blocks_before_client_call(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = SpyCtClient([_cert()])
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="other.net")
    assert result.status == "blocked"
    assert result.error == "target_out_of_scope"
    assert client.calls == []


@pytest.mark.asyncio
async def test_third_party_san_escalates_to_needs_review(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = SpyCtClient([_cert(sans=["www.example.com", "partner.thirdparty.net"])])
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    assert result.status == "completed"
    payload = _payload(db)[0]
    assert payload["related_scope_status"] == "needs_review"


@pytest.mark.asyncio
async def test_third_party_common_name_also_escalates(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = SpyCtClient([_cert(common_name="cdn.other-provider.org", sans=["www.example.com"])])
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    payload = _payload(db)[0]
    assert result.status == "completed"
    assert payload["related_scope_status"] == "needs_review"


@pytest.mark.asyncio
async def test_all_in_scope_sans_stay_candidate_never_confirmed(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = SpyCtClient([_cert()])
    await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    payload = _payload(db)[0]
    # CT matches alone never confirm ownership - even fully in-scope certs stay candidate
    assert payload["related_scope_status"] == "candidate"
    assert payload["related_scope_status"] != "confirmed"


@pytest.mark.asyncio
async def test_wildcard_san_classified_by_its_base_domain(tmp_path):
    scope = _scope(authorized_domains=("example.com",))
    db = _setup(tmp_path, scope)
    client = SpyCtClient([_cert(common_name="*.example.com", sans=["*.example.com", "example.com"])])
    result = await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    assert result.status == "completed"
    payload = _payload(db)[0]
    assert payload["related_scope_status"] == "candidate"


@pytest.mark.asyncio
async def test_ct_observations_never_create_assets_or_confirmed_direct(tmp_path):
    scope = _scope()
    db = _setup(tmp_path, scope)
    client = SpyCtClient([_cert(), _cert(sans=["edge.partner-cdn.io"], serial="ff00")])
    await run_ct_search(db, client, task_id="t", scope=scope, domain="example.com")
    assert next(db.rows("SELECT COUNT(*) AS count FROM asset"))["count"] == 0
    for payload in _payload(db):
        assert payload["related_scope_status"] in {"candidate", "needs_review"}
