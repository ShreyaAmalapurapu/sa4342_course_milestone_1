#!/usr/bin/env bash
# Runs the rules 15-31,52-55,63-65 of the channels check (see channels-N-*.md).
PY=/opt/test-venv/bin/python
[ -x "$PY" ] || PY=python3
CHECK_RULES="15-31,52-55,63-65" exec "$PY" "$(dirname "${BASH_SOURCE[0]}")/check_channels_core.py"
