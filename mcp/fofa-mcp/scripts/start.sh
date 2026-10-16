#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'uv was not found on PATH. Install uv, then run: uv sync' >&2
  exit 127
fi
exec uv run fofa-mcp
