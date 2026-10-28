#!/usr/bin/env bash
# All rules of the channels check, one total.
PY=/opt/test-venv/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" "$(dirname "${BASH_SOURCE[0]}")/check_channels_core.py"
