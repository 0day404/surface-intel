import pytest

from surface_intel.dns_runner import run_dns_lookup
from surface_intel.policy.scope import Scope
from surface_intel.state.database import Database


class RecordingClient:
    """Records every attempted query; answers nothing but NXDOMAIN."""

    def __init__(self):
        self.calls = []

    async def resolve(self, name, record_type):
        self.calls.append((name, record_type))
        return {"status": "nxdomain", "records": []}


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
async def test_unconfirmed_scope_blocks_before_any_resolver_call(tmp_path):
    scope = Scope(authorized_domains=("example.com",), confirmed=False)
    db = _setup(tmp_path, scope)
    client = RecordingClient()
    result = await run_dns_lookup(db, client, task_id="t", scope=scope, domain="www.example.com")
    assert result.status == "blocked"
    assert result.error == "scope_not_confirmed"
    assert client.calls == []
    assert next(db.rows("SELECT COUNT(*) AS count FROM observation"))["count"] == 0


@pytest.mark.asyncio
async def test_out_of_scope_domain_blocked_before_any_resolver_call(tmp_path):
    scope = Scope(authorized_domains=("example.com",), confirmed=True, scope_version="v1")
    db = _setup(tmp_path, scope)
    client = RecordingClient()
    result = await run_dns_lookup(db, client, task_id="t", scope=scope, domain="www.other.com")
    assert result.status == "blocked"
    assert result.error == "target_out_of_scope"
    assert client.calls == []


@pytest.mark.asyncio
async def test_excluded_target_wins_over_authorized_domain(tmp_path):
    scope = Scope(
        authorized_domains=("*.example.com",),
        excluded_targets=("secret.example.com",),
        confirmed=True,
        scope_version="v1",
    )
    db = _setup(tmp_path, scope)
    client = RecordingClient()
    result = await run_dns_lookup(db, client, task_id="t", scope=scope, domain="secret.example.com")
    assert result.status == "blocked"
    assert result.error == "target_out_of_scope"
    assert client.calls == []


@pytest.mark.asyncio
async def test_third_party_cname_values_stay_candidate_and_create_no_assets(tmp_path):
    scope = Scope(authorized_domains=("www.example.com",), confirmed=True, scope_version="v1")
    db = _setup(tmp_path, scope)

    class CnameClient(RecordingClient):
        async def resolve(self, name, record_type):
            self.calls.append((name, record_type))
            if record_type == "CNAME":
                return {"status": "observed", "records": ["third-party.cdn.example.net"]}
            if record_type == "NS":
                return {"status": "observed", "records": ["ns1.provider.net"]}
            return {"status": "observed", "records": ["203.0.113.9"]}

    client = CnameClient()
    result = await run_dns_lookup(
        db, client, task_id="t", scope=scope, domain="www.example.com",
        record_types=["A", "CNAME", "NS"],
    )
    assert result.status == "completed"
    # every resolved value points outside the authorized root and stays candidate
    rows = list(db.rows("SELECT payload_json FROM observation"))
    for row in rows:
        payload = __import__("json").loads(row["payload_json"])
        if payload["record_type"] in {"A", "CNAME", "NS"}:
            assert payload["related_scope_status"] == "candidate"
    assert next(db.rows("SELECT COUNT(*) AS count FROM asset"))["count"] == 0
    # the third-party CNAME target and provider NS were never resolved
    queried_names = {name for name, _ in client.calls}
    assert queried_names == {"www.example.com"}


@pytest.mark.asyncio
async def test_no_review_escalation_to_confirmed_direct(tmp_path):
    """DNS observations alone never assert architecture confirmation."""
    import json

    scope = Scope(authorized_domains=("example.com",), confirmed=True, scope_version="v1")
    db = _setup(tmp_path, scope)
    client = RecordingClient()
    await run_dns_lookup(db, client, task_id="t", scope=scope, domain="example.com")
    rows = list(db.rows("SELECT payload_json FROM observation"))
    for row in rows:
        payload = json.loads(row["payload_json"])
        assert "confirmed_direct" not in json.dumps(payload)
        assert payload["source"] == "dns_resolver"
