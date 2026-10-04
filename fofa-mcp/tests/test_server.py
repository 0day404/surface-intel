import asyncio
import re

from fofa_mcp.errors import PolicyDeniedError
from fofa_mcp.server import _structured_query, fofa_health
from fofa_mcp.policy import validate_output_format, validate_task_id


def test_structured_query_escapes_values_and_omits_empty_status():
    assert _structured_query('example.com', "", "443", "", 'a"b', "", "", "") == (
        'domain="example.com" && port="443" && body="a\\"b"'
    )


def test_health_returns_supplied_request_id():
    result = asyncio.run(fofa_health(request_id="req-test-01"))
    assert result["ok"] is True
    assert result["request_id"] == "req-test-01"
    assert result["task_id"] is None
    assert result["configuration"]["env_file_exists"] is True
    assert "budget" in result
    assert result["accounts_configured"] >= 1
    assert result["tool_schema_version"] == 1
    assert result["result_schema_version"] == 1
    assert result["config_schema_version"] == 1


def test_health_generates_request_id_without_exposing_configuration():
    result = asyncio.run(fofa_health())
    assert re.fullmatch(r"req-[0-9a-f]{32}", result["request_id"])
    assert "key" not in result


def test_invalid_tool_arguments_use_stable_policy_errors():
    for validator, value in (
        (validate_output_format, "invalid"),
        (validate_task_id, "bad task"),
    ):
        try:
            validator(value)
        except PolicyDeniedError as error:
            assert error.error_type == "policy_denied"
        else:
            raise AssertionError("expected PolicyDeniedError")
