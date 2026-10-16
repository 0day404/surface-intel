$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Push-Location $Root
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Error "uv was not found on PATH. Install uv, then run: uv sync"
        exit 127
    }
    & uv run fofa-mcp
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
