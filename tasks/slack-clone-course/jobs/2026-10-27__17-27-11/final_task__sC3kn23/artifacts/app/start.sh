#!/bin/sh
set -eu
mkdir -p /app
python3 - <<'PY'
import sys
sys.path.insert(0, '/app')
import server
server.init()
PY
pids=""
for p in 8000 8001 8002; do PORT=$p python3 /app/server.py & pids="$pids $!"; done
trap 'kill $pids 2>/dev/null || true' EXIT INT TERM
wait
