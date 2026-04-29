#!/usr/bin/env bash
# vbox-linux-debug setup — idempotent bootstrap.
# Delegates the heavy lifting to deps/setup_vbox.sh which provisions VBox +
# Extension Pack + cloud-image + cloud-init seed + VDI + VM. Re-run safely.

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"

exec bash "$VBLD_SKILL_ROOT/deps/setup_vbox.sh" "$@"
