from __future__ import annotations

import inspect
import tomllib
from pathlib import Path

import fofa_mcp
from fofa_mcp.version import SERVER_VERSION


def test_version_single_source():
    assert fofa_mcp.__version__ == SERVER_VERSION
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["version"] == SERVER_VERSION


def test_user_agent_is_built_from_server_version():
    from fofa_mcp import client

    source = inspect.getsource(client)
    assert 'f"fofa-mcp/{SERVER_VERSION}"' in source
