#!/usr/bin/env bash
# Runs the rules 20-40,64,65 of the roles check (see roles-N-*.md).
CHECK_RULES="20-40,64,65" exec bash "$(dirname "${BASH_SOURCE[0]}")/check_roles_core.sh"
