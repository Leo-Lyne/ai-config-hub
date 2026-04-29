#!/usr/bin/env bash
# Centralized config + auto-detection for /rk3568-flash skill.
# All script files source this; nothing else should hardcode paths.
#
# Override anything via environment variables:
#   RK_BSP_ROOT             — path to your Rockchip BSP repo (default: walk up from $PWD)
#   RK_IMAGE_DIR            — full-flash IMAGES dir (default: newest <bsp>/IMAGE/*/IMAGES)
#   RK_ROCKDEV_DIR          — single-partition images dir (default: newest <bsp>/rockdev/Image-*)
#   RK_UTOOL_EXE            — Windows upgrade_tool.exe path (default: glob from BSP RKTools/)
#   RK_UTOOL_LINUX          — Linux upgrade_tool path (default: glob from BSP, fallback deps/)
#   RK_WIN_CACHE_LX/_W      — Windows-accessible cache, WSL+Windows view
#   RK_VBOX_BIN             — VBoxManage path
#   RK_VBOX_VM_NAME         — VM name (default: rk-burn)
#   RK_VBOX_DIR_LX/_W       — VBox VMs folder, WSL+Windows view
#   RK_VBOX_SSH_PORT/_USER  — SSH access into the VM
#   RK_VBOX_VDI_LX/_W       — VM disk path, WSL+Windows view
#   RK_VBOX_NIC_MAC         — fixed MAC for VM (must match cloud-init netplan seal)
#   RK_VBOX_VM_MEMORY/_CPUS — VM hardware sizing
#   RK_ADB_HOST             — host-side adb binary
#   RK_STATE_DIR            — persistent state cache (mtime baselines etc.)

set -eu

# ─────────────────────────────────────────────────────────────────────────────
# Logging helpers (define early so detection functions can use)
# ─────────────────────────────────────────────────────────────────────────────
log() { printf '[rk3568-flash] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# ─────────────────────────────────────────────────────────────────────────────
# Skill self-location (for finding deps/, lib/, scripts/)
# ─────────────────────────────────────────────────────────────────────────────
RK_SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ─────────────────────────────────────────────────────────────────────────────
# Project root: walk up from $PWD until we find a Rockchip BSP marker
# ─────────────────────────────────────────────────────────────────────────────
_detect_bsp_root() {
    local d="${1:-$PWD}"
    while [ "$d" != "/" ] && [ -n "$d" ]; do
        # A Rockchip BSP repo has both build.sh AND RKTools/ at the root.
        if [ -f "$d/build.sh" ] && [ -d "$d/RKTools" ]; then
            printf '%s\n' "$d"; return 0
        fi
        d="$(dirname "$d")"
    done
    return 1
}
RK_BSP_ROOT="${RK_BSP_ROOT:-$(_detect_bsp_root || true)}"

# ─────────────────────────────────────────────────────────────────────────────
# Image dirs (BSP build outputs)
# ─────────────────────────────────────────────────────────────────────────────
_newest_dir() { ls -1dt "$@" 2>/dev/null | head -1; }

if [ -z "${RK_IMAGE_DIR:-}" ] && [ -n "${RK_BSP_ROOT:-}" ]; then
    RK_IMAGE_DIR="$(_newest_dir "$RK_BSP_ROOT"/IMAGE/*/IMAGES)"
fi
if [ -z "${RK_ROCKDEV_DIR:-}" ] && [ -n "${RK_BSP_ROOT:-}" ]; then
    RK_ROCKDEV_DIR="$(_newest_dir "$RK_BSP_ROOT"/rockdev/Image-*)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# upgrade_tool binaries — Windows .exe and Linux v2.4
# ─────────────────────────────────────────────────────────────────────────────
if [ -z "${RK_UTOOL_EXE:-}" ] && [ -n "${RK_BSP_ROOT:-}" ]; then
    RK_UTOOL_EXE="$(_newest_dir "$RK_BSP_ROOT"/RKTools/windows/*upgrade_tool*v*/upgrade_tool.exe 2>/dev/null)"
fi

if [ -z "${RK_UTOOL_LINUX:-}" ]; then
    # 1. BSP-bundled (preferred — version matches the project's loader)
    if [ -n "${RK_BSP_ROOT:-}" ]; then
        cand="$(_newest_dir "$RK_BSP_ROOT"/RKTools/linux/Linux_Upgrade_Tool/Linux_Upgrade_Tool_v*/upgrade_tool 2>/dev/null)"
        if [ -n "$cand" ] && [ -x "$cand" ]; then
            RK_UTOOL_LINUX="$cand"
        fi
    fi
    # 2. deps/-bundled fallback
    if [ -z "${RK_UTOOL_LINUX:-}" ] && [ -x "$RK_SKILL_ROOT/deps/linux_upgrade_tool/upgrade_tool" ]; then
        RK_UTOOL_LINUX="$RK_SKILL_ROOT/deps/linux_upgrade_tool/upgrade_tool"
    fi
fi

# Auto-extract from BSP zip if available but not unzipped
_extract_linux_utool_from_zip() {
    [ -n "${RK_BSP_ROOT:-}" ] || return 1
    local zip; zip="$(_newest_dir "$RK_BSP_ROOT"/RKTools/linux/Linux_Upgrade_Tool/Linux_Upgrade_Tool_v*.zip 2>/dev/null)"
    [ -n "$zip" ] && [ -f "$zip" ] || return 1
    local dest; dest="$(dirname "$zip")"
    log "Extracting $zip → $dest..."
    (cd "$dest" && unzip -o -q "$zip") || return 1
    RK_UTOOL_LINUX="$(_newest_dir "$dest"/Linux_Upgrade_Tool_v*/upgrade_tool 2>/dev/null)"
    [ -x "$RK_UTOOL_LINUX" ]
}

# ─────────────────────────────────────────────────────────────────────────────
# Windows-accessible cache (for upgrade_tool.exe — can't read WSL ext4)
# Default to D:\, override RK_WIN_CACHE_LX / RK_WIN_CACHE_W to use a different drive
# ─────────────────────────────────────────────────────────────────────────────
RK_WIN_CACHE_LX="${RK_WIN_CACHE_LX:-/mnt/d/rk3568-flash/cache}"
RK_WIN_CACHE_W="${RK_WIN_CACHE_W:-D:\\rk3568-flash\\cache}"

# ─────────────────────────────────────────────────────────────────────────────
# VirtualBox config (for vbox-linux transport)
# ─────────────────────────────────────────────────────────────────────────────
RK_VBOX_BIN="${RK_VBOX_BIN:-/mnt/c/Program Files/Oracle/VirtualBox/VBoxManage.exe}"
RK_VBOX_VM_NAME="${RK_VBOX_VM_NAME:-rk-burn}"
RK_VBOX_DIR_LX="${RK_VBOX_DIR_LX:-/mnt/d/VBoxVMs}"
RK_VBOX_DIR_W="${RK_VBOX_DIR_W:-D:\\VBoxVMs}"
RK_VBOX_VDI_LX="${RK_VBOX_VDI_LX:-$RK_VBOX_DIR_LX/${RK_VBOX_VM_NAME}.vdi}"
RK_VBOX_VDI_W="${RK_VBOX_VDI_W:-$RK_VBOX_DIR_W\\${RK_VBOX_VM_NAME}.vdi}"
RK_VBOX_VM_MEMORY="${RK_VBOX_VM_MEMORY:-1024}"
RK_VBOX_VM_CPUS="${RK_VBOX_VM_CPUS:-1}"
# MAC must match cloud-init's netplan match: macaddress on FIRST boot;
# default is what our seed.iso template tells cloud-init to use.
RK_VBOX_NIC_MAC="${RK_VBOX_NIC_MAC:-080027F1A53C}"
RK_VBOX_SSH_HOST="${RK_VBOX_SSH_HOST:-127.0.0.1}"
RK_VBOX_SSH_PORT="${RK_VBOX_SSH_PORT:-2222}"
RK_VBOX_SSH_USER="${RK_VBOX_SSH_USER:-${USER:-rkflash}}"
RK_VBOX_SSH_KEY="${RK_VBOX_SSH_KEY:-$HOME/.ssh/id_ed25519}"

# Path inside the VM where Linux upgrade_tool gets staged (one-time SCP)
RK_VBOX_UTOOL_VM="${RK_VBOX_UTOOL_VM:-/home/$RK_VBOX_SSH_USER/upgrade_tool_v24}"

# ─────────────────────────────────────────────────────────────────────────────
# adb binaries (used to drive Android → Loader transition)
#
# Two scopes:
#   RK_ADB_HOST    — WSL/Linux-side adb. Sees device only if usbip-attached or
#                    network-adb. Used by vbox-linux transport.
#   RK_ADB_WIN_EXE — Windows-side adb.exe. Sees device when Windows holds the
#                    USB claim (the common case for windows transport — Rockchip
#                    driver lives on Windows). Auto-detected from RKTools.
# ─────────────────────────────────────────────────────────────────────────────
RK_ADB_HOST="${RK_ADB_HOST:-$(command -v adb 2>/dev/null || true)}"

if [ -z "${RK_ADB_WIN_EXE:-}" ] && [ -n "${RK_BSP_ROOT:-}" ]; then
    cand="$RK_BSP_ROOT/RKTools/windows/adb_fastboot/adb.exe"
    [ -x "$cand" ] && RK_ADB_WIN_EXE="$cand"
fi
# usbipd-win bridge (for transport_vbox_linux and as a fallback to surface
# Windows-bound Rockusb devices into WSL). Empty string = not installed.
RK_USBIPD_EXE="${RK_USBIPD_EXE:-}"
if [ -z "$RK_USBIPD_EXE" ] && [ -x /mnt/c/Windows/System32/cmd.exe ]; then
    if /mnt/c/Windows/System32/cmd.exe /c "where usbipd" >/dev/null 2>&1; then
        RK_USBIPD_EXE="/mnt/c/Windows/System32/cmd.exe /c usbipd"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Persistent state (auto-flash baselines, etc.)
# ─────────────────────────────────────────────────────────────────────────────
RK_STATE_DIR="${RK_STATE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/rk3568-flash}"
mkdir -p "$RK_STATE_DIR" 2>/dev/null || true

# ─────────────────────────────────────────────────────────────────────────────
# Validation helpers (call as needed from each script)
# ─────────────────────────────────────────────────────────────────────────────
require_bsp_root() {
    [ -n "${RK_BSP_ROOT:-}" ] || die "no Rockchip BSP detected.
  cd into your BSP repo (one with build.sh + RKTools/), or set RK_BSP_ROOT=/path/to/bsp"
    [ -d "$RK_BSP_ROOT" ] || die "RK_BSP_ROOT=$RK_BSP_ROOT does not exist"
}

require_image_dir() {
    if [ -z "${RK_IMAGE_DIR:-}" ] || [ ! -d "$RK_IMAGE_DIR" ]; then
        die "no IMAGE dir found. Build with \`./build.sh -p\` first, or set RK_IMAGE_DIR=/path/to/IMAGES"
    fi
}

require_rockdev_dir() {
    if [ -z "${RK_ROCKDEV_DIR:-}" ] || [ ! -d "$RK_ROCKDEV_DIR" ]; then
        die "no rockdev/Image-* dir found. Build with \`./build.sh -p\` first, or set RK_ROCKDEV_DIR=/path/to/rockdev"
    fi
}

require_utool_exe() {
    [ -n "${RK_UTOOL_EXE:-}" ] && [ -x "$RK_UTOOL_EXE" ] && return 0
    die "Windows upgrade_tool.exe not found.
  Expected: <bsp>/RKTools/windows/win_upgrade_tool_v*/upgrade_tool.exe
  Override: export RK_UTOOL_EXE=/mnt/c/path/to/upgrade_tool.exe"
}

require_utool_linux() {
    [ -n "${RK_UTOOL_LINUX:-}" ] && [ -x "$RK_UTOOL_LINUX" ] && return 0
    # try extract from zip if present
    _extract_linux_utool_from_zip 2>/dev/null && return 0
    die "Linux upgrade_tool (v2.4) not found.
  Expected (in BSP):  <bsp>/RKTools/linux/Linux_Upgrade_Tool/Linux_Upgrade_Tool_v*/upgrade_tool
  Or bundled in skill: $RK_SKILL_ROOT/deps/linux_upgrade_tool/upgrade_tool
  Run: bash $RK_SKILL_ROOT/deps/setup_vbox.sh   to provision."
}

require_vbox() {
    [ -x "$RK_VBOX_BIN" ] || die "VBoxManage not found at $RK_VBOX_BIN.
  Install: winget install Oracle.VirtualBox  (or override RK_VBOX_BIN)"
}

# ─────────────────────────────────────────────────────────────────────────────
# Public summary (for status/debug output)
# ─────────────────────────────────────────────────────────────────────────────
print_config_summary() {
    cat >&2 <<EOS
[rk3568-flash] config:
  RK_BSP_ROOT       = ${RK_BSP_ROOT:-(not detected)}
  RK_IMAGE_DIR      = ${RK_IMAGE_DIR:-(not found)}
  RK_ROCKDEV_DIR    = ${RK_ROCKDEV_DIR:-(not found)}
  RK_UTOOL_EXE      = ${RK_UTOOL_EXE:-(not found — Windows transport unavailable)}
  RK_UTOOL_LINUX    = ${RK_UTOOL_LINUX:-(not found — vbox-linux transport unavailable until provisioned)}
  RK_VBOX_BIN       = $([ -x "$RK_VBOX_BIN" ] && echo "$RK_VBOX_BIN" || echo "(not installed — vbox-linux transport unavailable)")
  RK_VBOX_VM_NAME   = $RK_VBOX_VM_NAME
  RK_VBOX_SSH_USER  = $RK_VBOX_SSH_USER
  RK_ADB_HOST       = ${RK_ADB_HOST:-(no WSL-side adb in PATH)}
  RK_ADB_WIN_EXE    = ${RK_ADB_WIN_EXE:-(not found — auto-loader-entry will fall back to manual MaskRom)}
  RK_USBIPD_EXE     = ${RK_USBIPD_EXE:-(not installed — vbox-linux transport will use VBox USB filter only)}
  RK_STATE_DIR      = $RK_STATE_DIR
EOS
}
