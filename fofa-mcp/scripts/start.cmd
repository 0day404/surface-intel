@echo off
setlocal
cd /d "%~dp0.."
where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found on PATH. Install uv, then run: uv sync 1>&2
  exit /b 127
)
uv run fofa-mcp
exit /b %ERRORLEVEL%
