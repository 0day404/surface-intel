"""B6: fofa-search accepts comma-separated domains as one OR query.

A single value keeps the byte-identical query as before; every domain is
individually scope-checked and out-of-scope values reject the whole call.
"""
import json
from types import SimpleNamespace

import surface_intel.cli as cli
from surface_intel.state.database import Database


def _seed(tmp_path):
    db = Database(tmp_path / "task.sqlite")
    db.initialize()
    payload = {"scope_version": "scope-v1", "confirmed": True,
               "authorized_domains": ["example.com", "example.net"]}
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO task(task_id,status,schema_version,scope_version) "
            "VALUES('t','planned','1.0','scope-v1')")
        connection.execute(
            "INSERT INTO scope_snapshot(scope_id,task_id,scope_version,payload_json,confirmed) "
            "VALUES('s1','t','scope-v1',?,1)", (json.dumps(payload),))
    return db


def _run(tmp_path, monkeypatch, capsys, domain):
    captured = {}

    class StubClient:
        async def close(self):
            pass

    async def fake_run_fofa_query(db, client, *, task_id, scope_version, query, context, size):
        captured["query"] = query
        return SimpleNamespace(status="completed", observations=0, pages=1, batch_id="b",
                               consumed_fpoints=0, required_fpoints=0, cost_units=0, error=None)

    monkeypatch.setattr(cli, "configured_fofa_client", lambda: StubClient())
    monkeypatch.setattr(cli, "run_fofa_query", fake_run_fofa_query)
    exit_code = cli.main(["fofa-search", "t", "--task-root", str(tmp_path),
                          "--domain", domain, "--real", "--confirm-real"])
    output = json.loads(capsys.readouterr().out)
    return exit_code, output, captured


def test_multi_domain_builds_single_or_query(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    exit_code, output, captured = _run(tmp_path, monkeypatch, capsys, "example.com,example.net")
    assert exit_code == 0
    assert captured["query"] == 'domain="example.com" || domain="example.net"'
    assert output["domains"] == ["example.com", "example.net"]


def test_single_domain_keeps_legacy_query(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    exit_code, output, captured = _run(tmp_path, monkeypatch, capsys, "example.com")
    assert exit_code == 0
    assert captured["query"] == 'domain="example.com"'
    assert output["domains"] == ["example.com"]


def test_out_of_scope_member_rejects_whole_call(tmp_path, monkeypatch, capsys):
    _seed(tmp_path)
    exit_code, output, _ = _run(tmp_path, monkeypatch, capsys, "example.com,evil.example.org")
    assert exit_code == 2
    assert output["error"] == "target_out_of_scope"
    assert output["domains"] == ["evil.example.org"]
