#!/usr/bin/env bash
# Back-compat shim — forwards to the new dispatcher.
# Prefer: bash <skill>/scripts/flash.sh [<transport>] full [args]
exec bash "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/flash.sh" full "$@"
