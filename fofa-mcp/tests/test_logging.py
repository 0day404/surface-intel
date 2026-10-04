import logging

from fofa_mcp.logging import redact_log_text


def test_log_redaction_removes_credentials_and_keeps_diagnostics():
    message = (
        "tool=fofa_search request_id=req-1 "
        "https://fofa.test/api?email=a@example.com&key=secret-key "
        "authorization=Bearer secret-token"
    )
    redacted = redact_log_text(message)
    assert "secret-key" not in redacted
    assert "secret-token" not in redacted
    assert "request_id=req-1" in redacted


def test_logging_does_not_emit_to_stdout(capsys):
    logger = logging.getLogger("fofa-mcp-test")
    logger.warning("key=secret-key")
    captured = capsys.readouterr()
    assert captured.out == ""
