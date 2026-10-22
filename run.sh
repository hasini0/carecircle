#!/usr/bin/env bash
# One command: install, seed demo data, start the MCP server (:8000) and the Alexa+ simulator (:3000).
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/pip install -q -r requirements.txt
[ "$1" = "--keep-data" ] || .venv/bin/python scripts/seed_demo.py
.venv/bin/python server/carecircle_server.py & SERVER=$!
trap 'kill $SERVER 2>/dev/null' EXIT
sleep 2
echo "Open http://localhost:3000  (simulator)   and   http://localhost:8000/dashboard  (family view)"
.venv/bin/python simulator/app.py
