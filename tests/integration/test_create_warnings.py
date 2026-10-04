"""A1-3: `create` warns when the scope document claims active testing but
lacks a usable level - the fail-closed runtime gate is unchanged."""
import json

from surface_intel.cli import main


def _create(tmp_path, scope):
    path = tmp_path / "scope.json"
    path.write_text(json.dumps(scope), encoding="utf-8")
    return main(["create", "--task-root", str(tmp_path), "--scope", str(path)])


def test_active_allowed_without_level_warns(tmp_path, capsys):
    code = _create(tmp_path, {"confirmed": True, "authorized_domains": ["example.com"],
                              "active_testing_allowed": True})
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["warnings"] == ["active_testing_allowed_without_level"]


def test_valid_active_scope_has_no_warnings(tmp_path, capsys):
    code = _create(tmp_path, {"confirmed": True, "authorized_domains": ["example.com"],
                              "active_testing_allowed": True,
                              "active_testing_level": "safe_discovery",
                              "allowed_ports": [80, 443]})
    assert code == 0
    assert json.loads(capsys.readouterr().out)["warnings"] == []
