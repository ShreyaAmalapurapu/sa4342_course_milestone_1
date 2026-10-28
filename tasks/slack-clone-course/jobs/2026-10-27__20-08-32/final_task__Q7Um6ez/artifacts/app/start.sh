#!/bin/sh
set -eu
mkdir -p /app/data
pids=""
for p in 8000 8001 8002; do
  python3 /app/app.py "$p" &
  pids="$pids $!"
done
trap 'kill $pids 2>/dev/null || true; wait' TERM INT
wait
