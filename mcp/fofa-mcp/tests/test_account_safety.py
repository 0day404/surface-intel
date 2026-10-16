from fofa_mcp.server import _sanitize_account_info


def test_account_info_sanitizes_keys_and_redacts_email():
    result = _sanitize_account_info(
        {
            "email": "analyst@example.com",
            "fpoint": 12,
            "key": "must-not-return",
            "nested": {"access_token": "must-not-return"},
        }
    )
    assert result == {
        "email": "a***@example.com",
        "fpoint": 12,
        "nested": {},
    }
