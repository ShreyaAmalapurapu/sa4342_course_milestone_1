#!/usr/bin/env bash
# All rules of the roles check, one total.
exec bash "$(dirname "${BASH_SOURCE[0]}")/check_roles_core.sh"
