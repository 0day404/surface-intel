"""R6.2 Safe Port client and runner: explicit-port-only verification."""
import json

import pytest

from surface_intel.policy.scope import ActiveLevel, Scope
from surface_intel.safe_port_client import SafePortProbeClient
from surface_intel.safe_port_runner import run_port_check
from surface_intel.state.database import Database


def _connect_ok(host, port, timeout):
    class FakeSock:
        def close(self):
            pass

    cert = b"der-cert" if port in {443, 8443} else None
    # D-3: (socket, peer_cert, banner, closed_after_accept) - stubs never greet
    return FakeSock(), cert, None, False


def _connect_refuse(host, port, timeout):
    raise ConnectionRefusedError


def _connect_timeout(host, port, timeout):
    raise TimeoutError


def _connect_os(host, port, timeout):
    raise OSError(113, "host unreachable")


@pytest.mark.asyncio
async def test_open_port_records_hint_and_tls_digest():
    client = SafePortProbeClient(connect=_connect_ok)
    outcome = await client.probe("www.example.com", 443)
    assert outcome["status"] == "open"
    assert outcome["service_hint"] == "https"
    assert outcome["service_confidence"] == 0.5  # hints are bounded guesses
    assert outcome["tls_sha256"] is not None
    assert outcome["tls_error"] is False


@pytest.mark.asyncio
async def test_closed_timeout_filtered_are_distinct():
    assert (await SafePortProbeClient(connect=_connect_refuse).probe("www.example.com", 443))["status"] == "closed"
    assert (await SafePortProbeClient(connect=_connect_timeout).probe("www.example.com", 443))["status"] == "timeout"
    assert (await SafePortProbeClient(connect=_connect_os).probe("www.example.com", 443))["status"] == "filtered"


@pytest.mark.asyncio
async def test_tls_handshake_failure_on_open_port_stays_open():
    def connect(host, port, timeout):
        raise __import__("ssl").SSLError("handshake")

    outcome = await SafePortProbeClient(connect=connect).probe("www.example.com", 443)
    assert outcome["status"] == "open"  # answered, handshake failed
    assert outcome["tls_error"] is True


def _setup(tmp_path, scope, assets=()):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) VALUES('t','planned','1.0',?)",
            (scope.scope_version,),
        )
        for asset_type, value, architecture in assets:
            connection.execute(
                """INSERT INTO asset(asset_id,task_id,asset_type,normalized_value,scope_status,architecture_status)
                   VALUES(?,?,?,?,?,?)""",
                (f"asset-{value}", "t", asset_type, value, "confirmed", architecture),
            )
    return db


def _active_scope(**overrides):
    defaults = dict(
        authorized_domains=("example.com", "*.example.com"),
        confirmed=True,
        scope_version="scope-v1",
        active_testing_allowed=True,
        active_testing_level=ActiveLevel.SAFE_DISCOVERY,
        allowed_ports=(443,),
    )
    defaults.update(overrides)
    return Scope(**defaults)


class StubPortClient:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    async def probe(self, host, port):
        self.calls.append((host, port))
        return dict(self.outcome)


def _open():
    return {"status": "open", "host": "www.example.com", "port": 443,
            "service_hint": "https", "service_confidence": 0.5,
            "tls_sha256": "c" * 64, "probe_ms": 5}


def _payload(db):
    return [json.loads(row["payload_json"]) for row in db.rows(
        "SELECT payload_json FROM observation ORDER BY rowid")]



@pytest.mark.asyncio
async def test_listed_port_open_stays_candidate(tmp_path):
    scope = _active_scope()
    db = _setup(tmp_path, scope)
    client = StubPortClient(_open())
    result = await run_port_check(db, client, task_id="t", scope=scope,
                                  target="www.example.com", port=443)
    assert result.status == "completed"
    payload = _payload(db)[0]
    assert payload["transport_status"] == "open"
    assert payload["related_scope_status"] == "candidate"  # open never confirms ownership
    assert payload["tls_sha256"] == "c" * 64
    assert payload["source"] == "safe_port"


@pytest.mark.asyncio
async def test_unlisted_and_adjacent_ports_refused_without_probing(tmp_path):
    scope = _active_scope(allowed_ports=(443,))
    db = _setup(tmp_path, scope)
    client = StubPortClient(_open())
    for port in (80, 444, 8443):  # unlisted AND adjacent to the listed one
        result = await run_port_check(db, client, task_id="t", scope=scope,
                                      target="www.example.com", port=port)
        assert result.status == "blocked", port
        assert result.error == "port_not_authorized"
    assert client.calls == []


@pytest.mark.asyncio
async def test_empty_allowed_ports_authorizes_nothing(tmp_path):
    scope = _active_scope(allowed_ports=())
    db = _setup(tmp_path, scope)
    client = StubPortClient(_open())
    result = await run_port_check(db, client, task_id="t", scope=scope,
                                  target="www.example.com", port=443)
    assert result.status == "blocked"
    assert result.error == "port_not_authorized"
    assert client.calls == []


@pytest.mark.asyncio
async def test_closed_port_is_definitive_negative(tmp_path):
    scope = _active_scope()
    db = _setup(tmp_path, scope)
    client = StubPortClient({"status": "closed", "host": "www.example.com", "port": 443})
    result = await run_port_check(db, client, task_id="t", scope=scope,
                                  target="www.example.com", port=443)
    assert result.status == "completed"
    assert _payload(db)[0]["related_scope_status"] == "not_applicable"


@pytest.mark.asyncio
async def test_architecture_gate_blocks_port_probing(tmp_path):
    scope = _active_scope()
    db = _setup(tmp_path, scope, assets=(("domain", "www.example.com", "cloud_hosting_likely"),))
    client = StubPortClient(_open())
    result = await run_port_check(db, client, task_id="t", scope=scope,
                                  target="www.example.com", port=443)
    assert result.status == "blocked"
    assert result.error == "architecture_blocked:cloud_hosting_likely"
    assert result.review_created is True
    assert client.calls == []
