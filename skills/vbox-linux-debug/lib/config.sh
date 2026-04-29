#!/usr/bin/env bash
# Centralized config + auto-detection for /vbox-linux-debug skill.
# All scripts source this; nothing else should hardcode paths.
#
# Override anything via environment variables:
#   VBLD_VBOX_BIN        — VBoxManage path (auto-detect: WSL2 → Win install; native → $PATH)
#   VBLD_VM_NAME         — VM name (default: rk-burn for back-compat)
#   VBLD_VM_DIR_LX       — VBox VMs folder, WSL/Linux view
#   VBLD_VM_DIR_W        — same dir, Windows-path view (auto-derived from _LX on WSL)
#   VBLD_VDI_LX/_W       — VM disk path, WSL/Win view (auto-derived from VM_DIR + VM_NAME)
#   VBLD_VM_MEMORY/_CPUS — VM hardware sizing
#   VBLD_VM_NIC_MAC      — fixed MAC for VM (must match cloud-init seal; if empty, deps/setup_vbox.sh seals a random one on first boot)
#   VBLD_SSH_HOST/_PORT  — NAT port-forward target (default 127.0.0.1:2222)
#   VBLD_SSH_USER        — SSH user inside the VM (default: $USER, must match cloud-init seed)
#   VBLD_SSH_KEY         — SSH key for VM access (default: $HOME/.ssh/id_ed25519)
#
# Per-host persistent overrides: ${XDG_CONFIG_HOME:-$HOME/.config}/vbox-linux-debug/env
# is sourced if present (one `KEY=value` per line, shell syntax).

set -eu

# ─────────────────────────────────────────────────────────────────────────────
# Logging helpers (define early so detection functions can use)
# ─────────────────────────────────────────────────────────────────────────────
log() { printf '[vbox-linux-debug] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# ─────────────────────────────────────────────────────────────────────────────
# Skill self-location (for finding deps/, lib/, scripts/)
# ─────────────────────────────────────────────────────────────────────────────
VBLD_SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ─────────────────────────────────────────────────────────────────────────────
# Per-host override file
# ─────────────────────────────────────────────────────────────────────────────
VBLD_OVERRIDE_FILE="${VBLD_OVERRIDE_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/vbox-linux-debug/env}"
if [ -f "$VBLD_OVERRIDE_FILE" ]; then
    # shellcheck disable=SC1090
    . "$VBLD_OVERRIDE_FILE"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Platform detection (WSL2 vs native Linux)
# ─────────────────────────────────────────────────────────────────────────────
_is_wsl() { grep -qi 'microsoft\|wsl' /proc/version 2>/dev/null; }

# ─────────────────────────────────────────────────────────────────────────────
# VBoxManage location: WSL2 → Win install; native → $PATH
# ─────────────────────────────────────────────────────────────────────────────
_detect_vbox_bin() {
    if _is_wsl; then
        local cands=(
            '/mnt/c/Program Files/Oracle/VirtualBox/VBoxManage.exe'
            '/mnt/c/Program Files (x86)/Oracle/VirtualBox/VBoxManage.exe'
        )
        for c in "${cands[@]}"; do
            [ -x "$c" ] && { printf '%s\n' "$c"; return 0; }
        done
        # Last resort — let it fail later with a clear error
        printf '%s\n' "${cands[0]}"
        return 0
    fi
    command -v VBoxManage 2>/dev/null || printf '%s\n' "VBoxManage"
}
VBLD_VBOX_BIN="${VBLD_VBOX_BIN:-$(_detect_vbox_bin)}"

# ─────────────────────────────────────────────────────────────────────────────
# VM identity + storage paths
# ─────────────────────────────────────────────────────────────────────────────
VBLD_VM_NAME="${VBLD_VM_NAME:-rk-burn}"

# VM dir: prefer existing user choice, fall back to a portable Windows-accessible default.
# On WSL2, VBoxManage.exe cannot read WSL ext4 paths — must live under /mnt/<drive>/.
_detect_vm_dir() {
    if _is_wsl; then
        local cands=(
            '/mnt/d/VBoxVMs'
            '/mnt/c/VBoxVMs'
            '/mnt/c/Users/Public/VBoxVMs'
        )
        for c in "${cands[@]}"; do
            [ -d "$c" ] && { printf '%s\n' "$c"; return 0; }
        done
        printf '%s\n' '/mnt/c/Users/Public/VBoxVMs'
        return 0
    fi
    printf '%s\n' "${HOME}/VBoxVMs"
}
VBLD_VM_DIR_LX="${VBLD_VM_DIR_LX:-$(_detect_vm_dir)}"

# Auto-derive Windows view of VM_DIR_LX on WSL2.
_lx_to_winpath() {
    if _is_wsl && have wslpath; then
        wslpath -w "$1" 2>/dev/null || printf '%s\n' "$1"
    else
        printf '%s\n' "$1"
    fi
}
VBLD_VM_DIR_W="${VBLD_VM_DIR_W:-$(_lx_to_winpath "$VBLD_VM_DIR_LX")}"
VBLD_VDI_LX="${VBLD_VDI_LX:-$VBLD_VM_DIR_LX/${VBLD_VM_NAME}.vdi}"
VBLD_VDI_W="${VBLD_VDI_W:-$(_lx_to_winpath "$VBLD_VDI_LX")}"

# ─────────────────────────────────────────────────────────────────────────────
# VM hardware
# ─────────────────────────────────────────────────────────────────────────────
VBLD_VM_MEMORY="${VBLD_VM_MEMORY:-1024}"
VBLD_VM_CPUS="${VBLD_VM_CPUS:-1}"
VBLD_VM_NIC_TYPE="${VBLD_VM_NIC_TYPE:-82540EM}"
# MAC: empty by default — deps/setup_vbox.sh will randomize one on first VM creation
# and seal it via cloud-init's network-config (matches by MAC). If you re-build
# the VDI from a fresh cloud image, regenerate this MAC AND re-seal netplan
# inside the guest. See references/known-issues.md.
VBLD_VM_NIC_MAC="${VBLD_VM_NIC_MAC:-}"

# ─────────────────────────────────────────────────────────────────────────────
# SSH access (NAT port-forward to guest:22)
# ─────────────────────────────────────────────────────────────────────────────
VBLD_SSH_HOST="${VBLD_SSH_HOST:-127.0.0.1}"
VBLD_SSH_PORT="${VBLD_SSH_PORT:-2222}"
VBLD_SSH_USER="${VBLD_SSH_USER:-${USER:-debug}}"
VBLD_SSH_KEY="${VBLD_SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_TARGET="${VBLD_SSH_USER}@${VBLD_SSH_HOST}"
SSH_OPTS=(-p "$VBLD_SSH_PORT"
          -o StrictHostKeyChecking=no
          -o UserKnownHostsFile=/dev/null
          -o ConnectTimeout=8)
# If a key exists, use it explicitly (BatchMode-friendly).
[ -f "$VBLD_SSH_KEY" ] && SSH_OPTS+=(-i "$VBLD_SSH_KEY")

# ─────────────────────────────────────────────────────────────────────────────
# Common predicates
# ─────────────────────────────────────────────────────────────────────────────
vbm_present()        { [ -x "$VBLD_VBOX_BIN" ] || command -v "$VBLD_VBOX_BIN" >/dev/null 2>&1; }
vm_exists()          { vbm_present && "$VBLD_VBOX_BIN" list vms 2>/dev/null | grep -q "\"$VBLD_VM_NAME\""; }
vm_running()         { vbm_present && "$VBLD_VBOX_BIN" list runningvms 2>/dev/null | grep -q "\"$VBLD_VM_NAME\""; }
extpack_installed()  { vbm_present && "$VBLD_VBOX_BIN" list extpacks 2>/dev/null | grep -q '^Pack no\.'; }

ssh_vm()      { ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"; }
scp_to_vm()   { scp -P "$VBLD_SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$@" "${SSH_TARGET}:"; }
scp_from_vm() { scp -P "$VBLD_SSH_PORT" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "${SSH_TARGET}:$1" "$2"; }

ssh_ready() {
    ssh "${SSH_OPTS[@]}" -o BatchMode=yes -o ConnectTimeout=4 "$SSH_TARGET" 'true' 2>/dev/null
}

wait_ssh() {
    local timeout="${1:-300}" t=0
    while ! ssh_ready; do
        sleep 6; t=$((t + 6))
        [ "$t" -ge "$timeout" ] && die "SSH not ready after ${timeout}s"
    done
}

# ─────────────────────────────────────────────────────────────────────────────
# Public summary (for status/debug output)
# ─────────────────────────────────────────────────────────────────────────────
print_config_summary() {
    cat >&2 <<EOS
[vbox-linux-debug] config:
  VBLD_VBOX_BIN     = $([ -x "$VBLD_VBOX_BIN" ] && echo "$VBLD_VBOX_BIN" || echo "(not installed — run \`setup\`)")
  VBLD_VM_NAME      = $VBLD_VM_NAME
  VBLD_VM_DIR_LX    = $VBLD_VM_DIR_LX
  VBLD_VDI_LX       = $VBLD_VDI_LX  $([ -f "$VBLD_VDI_LX" ] && echo "(present)" || echo "(missing — \`setup\` will build)")
  VBLD_VM_NIC_MAC   = ${VBLD_VM_NIC_MAC:-(unset; auto on first setup)}
  SSH               = $SSH_TARGET (port $VBLD_SSH_PORT)
  override file     = $([ -f "$VBLD_OVERRIDE_FILE" ] && echo "$VBLD_OVERRIDE_FILE (loaded)" || echo "(none)")
EOS
}
