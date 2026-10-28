#!/bin/sh
cd /app
python3 app.py 8000 >/tmp/huddle0.log 2>&1 & echo $! >/tmp/huddle0.pid
python3 app.py 8001 >/tmp/huddle1.log 2>&1 & echo $! >/tmp/huddle1.pid
python3 app.py 8002 >/tmp/huddle2.log 2>&1 & echo $! >/tmp/huddle2.pid
wait
