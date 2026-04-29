#!/usr/bin/env bash
# Install bundled usbipd-win MSI on the Windows host (called from WSL2).
# Requires admin — Windows will UAC-prompt once.
#
# Idempotent: if usbipd.exe is already on PATH, exits 0.

set -eu

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_DIR="$SKILL_ROOT/deps/packages"

log() { printf '[usbip/install_windows] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

if ! grep -qi 'microsoft\|wsl' /proc/version 2>/dev/null; then
    die "this script is WSL2-only — usbipd-win runs on the Windows host"
fi

if have usbipd.exe; then
    log "✓ usbipd.exe already on PATH"
    usbipd.exe --version | sed 's/^/  /'
    exit 0
fi

MSI="$(ls -1 "$PKG_DIR"/usbipd-win_*.msi 2>/dev/null | head -1)"
if [ -z "$MSI" ]; then
    log "no MSI in $PKG_DIR — running fetch_deps.sh first"
    bash "$SKILL_ROOT/deps/fetch_deps.sh"
    MSI="$(ls -1 "$PKG_DIR"/usbipd-win_*.msi 2>/dev/null | head -1)"
fi
[ -n "$MSI" ] || die "MSI still missing after fetch — check network"

WIN_PATH="$(wslpath -w "$MSI")"
log "installing $MSI on Windows host (UAC will prompt once)..."
log "  $WIN_PATH"

# /passive shows progress bar but no user prompts (except UAC). /qn would be silent.
cmd.exe /c "msiexec /i \"$WIN_PATH\" /passive /norestart" 2>/dev/null || die "msiexec failed (UAC denied?)"

# Reload PATH so usbipd.exe shows up in the current shell
hash -r 2>/dev/null || true

if have usbipd.exe; then
    log "✓ installed: $(usbipd.exe --version)"
else
    log "✓ installer launched. Open a NEW WSL terminal — Windows PATH inheritance refreshes per-session."
fi
