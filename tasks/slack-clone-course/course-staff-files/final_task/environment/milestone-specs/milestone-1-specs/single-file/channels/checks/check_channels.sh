#!/usr/bin/env bash
# Self-check for component B. Prints PASS/FAIL per rule in /spec/channels/channels.md.
# Usage: bash /spec/channels/checks/check_channels.sh      Exit 0 only if every rule passes.
PY=/opt/test-venv/bin/python
[ -x "$PY" ] || PY=python3
exec "$PY" "$(dirname "${BASH_SOURCE[0]}")/check_channels.py"
