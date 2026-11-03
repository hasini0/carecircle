#!/usr/bin/env bash
# One command: install, seed demo data, start CareCircle on http://localhost:8000
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
# Some venv tools (e.g. uv) create environments without pip; bootstrap it if missing.
.venv/bin/python -m pip --version >/dev/null 2>&1 || .venv/bin/python -m ensurepip >/dev/null
.venv/bin/python -m pip install -q -r requirements.txt
[ "$1" = "--keep-data" ] || .venv/bin/python scripts/seed_demo.py
echo "Open http://localhost:8000   (MCP endpoint: http://localhost:8000/mcp)"
exec .venv/bin/python server/carecircle_server.py
