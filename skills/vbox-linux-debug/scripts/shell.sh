#!/usr/bin/env bash
# Open SSH session into the VM, or run a one-shot command.
# Usage: shell                   # interactive
#        shell <cmd> [args...]   # exec one command (stdout returned)

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"
vbm_present || die "VirtualBox not installed."
vm_running  || die "VM not running. Run \`up\` first."
ssh_ready   || die "SSH not ready."

if [ $# -eq 0 ]; then
    exec ssh "${SSH_OPTS[@]}" "$SSH_TARGET"
else
    ssh_vm "$@"
fi
