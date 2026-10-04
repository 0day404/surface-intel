from pathlib import Path

import pytest

from fofa_mcp.config import AccountConfig, Settings
from fofa_mcp.errors import InvalidFieldError, InvalidQueryError, PolicyDeniedError
from fofa_mcp.policy import (
    normalize_fields,
    normalize_stats_fields,
    validate_query,
    validate_size,
)


def test_project_root_env_file_is_independent_of_current_directory(monkeypatch, tmp_path):
    from fofa_mcp.config import env_file_path, project_root

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FOFA_ENV_FILE", raising=False)
    assert env_file_path() == project_root() / ".env"


def test_env_file_override_is_resolved_relative_to_project_root(monkeypatch):
    from fofa_mcp.config import env_file_path, project_root

    monkeypatch.setenv("FOFA_ENV_FILE", "config/test.env")
    assert env_file_path() == project_root() / "config" / "test.env"


def settings() -> Settings:
    return Settings(
        api_base="https://fofa.info/api/v1",
        timeout_sec=30,
        default_page_size=100,
        max_page_size=1000,
        max_cursor_pages=20,
        max_requests_per_session=20,
        max_estimated_fpoints=30,
        max_total_results=2000,
        max_query_length=20,
        max_retries=1,
        max_host_response_bytes=2_000_000,
        log_level="INFO",
        accounts=(AccountConfig("primary", "", "key"),),
    )


def test_fields_default_and_all():
    assert "ip" in normalize_fields(None)
    assert len(normalize_fields(["all"])) > 10


def test_invalid_field_is_rejected():
    with pytest.raises(InvalidFieldError):
        normalize_fields(["not_a_fofa_field"])


def test_stats_fields_use_a_separate_allowlist():
    assert normalize_stats_fields(["port", "country"]) == ["port", "country"]
    with pytest.raises(InvalidFieldError, match="unsupported FOFA stats field"):
        normalize_stats_fields(["banner"])


def test_stats_fields_default_is_stable():
    assert normalize_stats_fields(None) == ["title", "country"]


def test_query_policy():
    with pytest.raises(InvalidQueryError):
        validate_query("", settings())
    with pytest.raises(PolicyDeniedError):
        validate_query("*", settings())


def test_size_policy():
    with pytest.raises(PolicyDeniedError):
        validate_size(1001, settings())


def test_host_policy_accepts_ipv4_ipv6_and_hostname():
    from fofa_mcp.policy import validate_host

    assert validate_host("192.0.2.1") == "192.0.2.1"
    assert validate_host("2001:db8::1") == "2001:db8::1"
    assert validate_host("[2001:db8::1]") == "2001:db8::1"
    assert validate_host("admin.example.com") == "admin.example.com"


def test_host_policy_rejects_path_injection():
    from fofa_mcp.policy import validate_host

    with pytest.raises(PolicyDeniedError):
        validate_host("example.com/../../secret")


def test_status_code_validation_accepts_ints_and_ranges():
    from fofa_mcp.policy import validate_status_codes

    assert validate_status_codes("200") == "200"
    assert validate_status_codes("200,301,302") == "200,301,302"
    assert validate_status_codes("200-299") == "200-299"


def test_status_code_validation_rejects_injection():
    import pytest

    from fofa_mcp.errors import PolicyDeniedError
    from fofa_mcp.policy import validate_port_list, validate_status_codes

    for validator, bad in (
        (validate_status_codes, '200 && country="CN"'),
        (validate_status_codes, "abc"),
        (validate_status_codes, "99"),
        (validate_port_list, "443; ls"),
        (validate_port_list, "0"),
        (validate_port_list, "70000"),
    ):
        with pytest.raises(PolicyDeniedError):
            validator(bad)


def test_port_list_validation_accepts_ranges():
    from fofa_mcp.policy import validate_port_list

    assert validate_port_list("80,443,8000-8100") == "80,443,8000-8100"
