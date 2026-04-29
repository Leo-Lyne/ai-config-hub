#!/usr/bin/env bash
# Start the VM headless. Idempotent.
# Usage: up [--wait[=SEC]]    (default --wait=300)

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"

vbm_present || die "VirtualBox not installed. Run \`setup\` first."
vm_exists   || die "VM '$VBLD_VM_NAME' not found. Run \`setup\` first."

WAIT=300
case "${1:-}" in
    --no-wait)  WAIT=0 ;;
    --wait)     WAIT=300 ;;
    --wait=*)   WAIT="${1#--wait=}" ;;
    "")         ;;
    *)          die "Unknown arg: $1" ;;
esac

if vm_running; then
    log "VM '$VBLD_VM_NAME' already running."
else
    log "Starting VM '$VBLD_VM_NAME' headless..."
    "$VBLD_VBOX_BIN" startvm "$VBLD_VM_NAME" --type headless | tail -1
fi

if [ "$WAIT" -gt 0 ]; then
    log "Waiting up to ${WAIT}s for SSH on ${VBLD_SSH_HOST}:${VBLD_SSH_PORT}..."
    wait_ssh "$WAIT"
    log "SSH ready: $(ssh_vm 'uname -srm; cat /etc/os-release | grep PRETTY_NAME')"
fi
