#!/usr/bin/env bash
# Stop the VM. Default: ACPI poweroff. --hard: force poweroff.
# Usage: down [--hard]

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"

vbm_present || die "VirtualBox not installed."
if ! vm_running; then
    log "VM '$VBLD_VM_NAME' not running."
    exit 0
fi

if [ "${1:-}" = "--hard" ]; then
    log "Force-poweroff $VBLD_VM_NAME..."
    "$VBLD_VBOX_BIN" controlvm "$VBLD_VM_NAME" poweroff | tail -3
else
    log "ACPI poweroff $VBLD_VM_NAME (graceful)..."
    "$VBLD_VBOX_BIN" controlvm "$VBLD_VM_NAME" acpipowerbutton | tail -3
    for _ in $(seq 1 20); do vm_running || break; sleep 2; done
    if vm_running; then
        log "Graceful poweroff timed out, forcing."
        "$VBLD_VBOX_BIN" controlvm "$VBLD_VM_NAME" poweroff | tail -3
    fi
fi
log "VM stopped."
