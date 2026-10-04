"""D1: ct-health stays network-free and honest about being unverified;
the opt-in --probe is the only path that touches the network and demands
the full real-call authorization pair."""
import json

import surface_intel.cli as cli


class StubCtClient:
    source_name = "stub-ct"

    def __init__(self):
        self.probe_calls = 0

    async def search(self, domain):
        raise AssertionError("ct-health must never search")

    async def health(self):
        return {"ok": True, "verified": False,
                "note": "configuration_check_only_no_network",
                "primary": {"ok": True, "verified": False},
                "fallback": {"ok": True, "verified": False},
                "adapter": "ct_log"}

    async def probe(self):
        self.probe_calls += 1
        return {"ok": True, "strategy": "failover",
                "primary": {"ok": True, "http_code": 200, "latency_ms": 5},
                "fallback": {"ok": True, "http_code": 200, "latency_ms": 7}}


def test_ct_health_reports_unverified_and_zero_network_requests(tmp_path, monkeypatch, capsys):
    client = StubCtClient()
    monkeypatch.setattr(cli, "configured_ct_client", lambda: client)
    code = cli.main(["ct-health", "--real"])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["network_requests_sent"] == 0
    assert output["verified"] is False
    assert output["detail"]["note"] == "configuration_check_only_no_network"
    assert client.probe_calls == 0


def test_probe_without_confirm_real_refused_and_sends_nothing(tmp_path, monkeypatch, capsys):
    client = StubCtClient()
    monkeypatch.setattr(cli, "configured_ct_client", lambda: client)
    code = cli.main(["ct-health", "--real", "--probe"])
    assert code == 2
    assert json.loads(capsys.readouterr().out)["error"] == "real_and_confirm_real_required"
    assert client.probe_calls == 0


def test_probe_with_full_authorization_runs_real_probe(tmp_path, monkeypatch, capsys):
    client = StubCtClient()
    monkeypatch.setattr(cli, "configured_ct_client", lambda: client)
    code = cli.main(["ct-health", "--real", "--confirm-real", "--probe"])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["probe"] is True
    assert output["network_requests_sent"] == 1
    assert client.probe_calls == 1
