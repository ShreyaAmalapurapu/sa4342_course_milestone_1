#!/bin/sh
mkdir -p /app/data
python3 /app/app.py 8000 0 &
p1=$!
python3 /app/app.py 8001 1 &
p2=$!
python3 /app/app.py 8002 2 &
p3=$!
trap 'kill $p1 $p2 $p3 2>/dev/null; exit 0' TERM INT
wait
