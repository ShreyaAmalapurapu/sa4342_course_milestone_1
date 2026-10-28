#!/usr/bin/env bash
# Runs the rules 32-39,61,62 of the channels check (see channels-N-*.md).
PY=/opt/test-venv/bin/python
[ -x "$PY" ] || PY=python3
CHECK_RULES="32-39,61,62" exec "$PY" "$(dirname "${BASH_SOURCE[0]}")/check_channels_core.py"
