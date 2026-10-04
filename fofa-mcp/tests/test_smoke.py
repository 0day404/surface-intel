from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_missing_env_reports_actionable_error_without_secret(tmp_path):
    env = os.environ.copy()
    for name in (
        "FOFA_KEY",
        "FOFA_EMAIL",
        "FOFA_KEY_BACKUP",
        "FOFA_EMAIL_BACKUP",
        "FOFA_KEY_BACKUP2",
        "FOFA_EMAIL_BACKUP2",
    ):
        env.pop(name, None)
    env["FOFA_ENV_FILE"] = str(tmp_path / "missing.env")
    result = subprocess.run(
        [sys.executable, "-c", "from fofa_mcp.config import Settings; Settings.load()"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "No FOFA key configured" in result.stderr or "No FOFA key configured" in result.stdout
    assert "FOFA_KEY" in result.stderr or "FOFA_KEY" in result.stdout
    assert "secret" not in result.stdout.lower()


def test_configured_env_loads_from_other_working_directory(tmp_path):
    env_file = tmp_path / "configured.env"
    env_file.write_text("FOFA_KEY=test-key\n", encoding="utf-8")
    env = os.environ.copy()
    env["FOFA_ENV_FILE"] = str(env_file)
    for name in ("FOFA_KEY", "FOFA_EMAIL", "FOFA_KEY_BACKUP", "FOFA_KEY_BACKUP2"):
        env.pop(name, None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from fofa_mcp.config import Settings; print(len(Settings.load().accounts))",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "1"
    assert result.stderr == ""


def test_module_and_console_startup_fail_consistently_without_credentials(tmp_path):
    root = Path(__file__).parents[1]
    env = os.environ.copy()
    for name in (
        "FOFA_KEY",
        "FOFA_EMAIL",
        "FOFA_KEY_BACKUP",
        "FOFA_EMAIL_BACKUP",
        "FOFA_KEY_BACKUP2",
        "FOFA_EMAIL_BACKUP2",
    ):
        env.pop(name, None)
    env["FOFA_ENV_FILE"] = str(tmp_path / "missing.env")
    commands = [
        [sys.executable, "-m", "fofa_mcp"],
        ["uv", "run", "--directory", str(root), "fofa-mcp"],
    ]
    results = [
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=env,
            cwd=root,
        )
        for command in commands
    ]
    assert [result.returncode for result in results] == [2, 2]
    assert all("No FOFA key configured" in result.stderr for result in results)
    assert all(result.stdout == "" for result in results)
