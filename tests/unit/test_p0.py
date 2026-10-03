import json

from surface_intel.cli import main
from surface_intel.redaction import redact
from surface_intel.state.database import Database


def test_init_and_health(tmp_path):
    assert main(["init", str(tmp_path)]) == 0
    health = Database(tmp_path / "task.sqlite").health()
    assert health["ok"] is True
    assert health["schema_version"] == 6


def test_unconfirmed_scope_is_rejected(tmp_path, capsys):
    main(["init", str(tmp_path)])
    capsys.readouterr()
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"authorized_domains": ["example.com"], "confirmed": False}), encoding="utf-8")
    result = main(["create", "--task-root", str(tmp_path), "--scope", str(scope)])
    assert result == 0
    task_id = json.loads(capsys.readouterr().out)["task_id"]
    assert main(["validate", "--task-root", str(tmp_path), task_id]) == 2


def test_empty_scope_is_rejected(tmp_path):
    main(["init", str(tmp_path)])
    scope = tmp_path / "scope.json"
    scope.write_text("{}", encoding="utf-8")
    assert main(["create", "--task-root", str(tmp_path), "--scope", str(scope)]) == 2


def test_sensitive_values_are_redacted():
    result = redact({"Authorization": "Bearer secret", "nested": {"token": "abc"}, "ok": "value"})
    assert result == {"Authorization": "[REDACTED]", "nested": {"token": "[REDACTED]"}, "ok": "value"}
