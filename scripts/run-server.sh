#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
uv run uvicorn youtok.api.main:app --host 127.0.0.1 --port 8000 --reload
