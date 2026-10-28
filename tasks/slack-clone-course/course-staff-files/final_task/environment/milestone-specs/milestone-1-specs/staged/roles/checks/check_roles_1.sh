#!/usr/bin/env bash
# Runs the rules 1-19,63,70 of the roles check (see roles-N-*.md).
CHECK_RULES="1-19,63,70" exec bash "$(dirname "${BASH_SOURCE[0]}")/check_roles_core.sh"
