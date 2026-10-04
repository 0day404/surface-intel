"""版本单源化：pyproject == surface_intel.__version__，UA 由版本号拼装。"""
import tomllib
from pathlib import Path

import surface_intel


def test_version_single_source():
    assert surface_intel.__version__ == "0.2.0"
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert data["project"]["version"] == surface_intel.__version__


def test_user_agents_are_built_from_version():
    import inspect

    from surface_intel import ct_client, fofa_client, playwright_client, safe_http_client

    for module in (ct_client, fofa_client, playwright_client, safe_http_client):
        source = inspect.getsource(module)
        assert "__version__" in source, module.__name__
        assert "0.1.0" not in source, module.__name__
