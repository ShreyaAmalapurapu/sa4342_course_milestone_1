#!/usr/bin/env bash
# Runs the rules 1-14,66 of the channels check (see channels-N-*.md).
PY=/opt/test-venv/bin/python
[ -x "$PY" ] || PY=python3
CHECK_RULES="1-14,66" exec "$PY" "$(dirname "${BASH_SOURCE[0]}")/check_channels_core.py"
