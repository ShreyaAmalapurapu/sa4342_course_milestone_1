#!/usr/bin/env bash
# Runs the rules 41-62,66-69 of the roles check (see roles-N-*.md).
CHECK_RULES="41-62,66-69" exec bash "$(dirname "${BASH_SOURCE[0]}")/check_roles_core.sh"
