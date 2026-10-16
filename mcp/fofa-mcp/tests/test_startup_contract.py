from __future__ import annotations

import tomllib
from pathlib import Path

from fofa_mcp import __main__
from fofa_mcp.server import main


def test_module_and_console_entrypoints_share_main():
    assert __main__.main is main


def test_console_entrypoint_targets_same_main():
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert project["project"]["scripts"]["fofa-mcp"] == "fofa_mcp.server:main"


def test_start_scripts_check_uv_and_use_primary_entrypoint():
    root = Path(__file__).parents[1]
    scripts = {
        "start.ps1": (root / "scripts" / "start.ps1").read_text(encoding="utf-8"),
        "start.cmd": (root / "scripts" / "start.cmd").read_text(encoding="utf-8"),
        "start.sh": (root / "scripts" / "start.sh").read_text(encoding="utf-8"),
    }
    assert "uv" in scripts["start.ps1"] and "uv run fofa-mcp" in scripts["start.ps1"]
    assert "where uv" in scripts["start.cmd"] and "uv run fofa-mcp" in scripts["start.cmd"]
    assert "command -v uv" in scripts["start.sh"] and "uv run fofa-mcp" in scripts["start.sh"]
    assert "FOFA_KEY" not in scripts["start.ps1"]
    assert "FOFA_KEY" not in scripts["start.cmd"]
    assert "FOFA_KEY" not in scripts["start.sh"]


def test_start_scripts_resolve_root_from_script_location():
    root = Path(__file__).parents[1]
    powershell = (root / "scripts" / "start.ps1").read_text(encoding="utf-8")
    command = (root / "scripts" / "start.cmd").read_text(encoding="utf-8")
    shell = (root / "scripts" / "start.sh").read_text(encoding="utf-8")

    assert "Split-Path -Parent $PSScriptRoot" in powershell
    assert 'cd /d "%~dp0.."' in command
    assert 'dirname -- "$0"' in shell
    assert "cd \"$ROOT\"" in shell


def test_root_resolution_works_after_changing_directory(monkeypatch, tmp_path):
    from fofa_mcp.config import project_root

    work_dir = tmp_path / "directory with spaces"
    work_dir.mkdir()
    monkeypatch.chdir(work_dir)
    assert project_root().name == "fofa-mcp"
