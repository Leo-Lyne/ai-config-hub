#!/usr/bin/env bash
# Bundle the usbipd-win MSI installer into deps/packages/ so the skill can be
# bootstrapped offline.
#
# Override version/URL via $USBIPD_VER / $USBIPD_MSI_URL.
# Default: a pinned LTS-stable release.

set -eu

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_DIR="$SKILL_ROOT/deps/packages"
mkdir -p "$PKG_DIR"

USBIPD_VER="${USBIPD_VER:-4.4.0}"
USBIPD_MSI_URL="${USBIPD_MSI_URL:-https://github.com/dorssel/usbipd-win/releases/download/v${USBIPD_VER}/usbipd-win_${USBIPD_VER}.msi}"

MSI="$PKG_DIR/usbipd-win_${USBIPD_VER}.msi"

log() { printf '[usbip/fetch_deps] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

if [ -f "$MSI" ] && [ "$(stat -c%s "$MSI" 2>/dev/null || echo 0)" -gt 100000 ]; then
    log "✓ already bundled: $MSI ($(du -h "$MSI" | cut -f1))"
    exit 0
fi

have curl || die "need curl"

log "downloading usbipd-win v${USBIPD_VER}"
log "  URL: $USBIPD_MSI_URL"
log "  dst: $MSI"
curl -fL --progress-bar -o "$MSI.partial" "$USBIPD_MSI_URL" || die "download failed"
mv "$MSI.partial" "$MSI"

# manifest
{
    echo "# usbip skill — usbipd-win bundle"
    echo "# generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "USBIPD_VER=$USBIPD_VER"
    echo "URL=$USBIPD_MSI_URL"
    echo "SHA256:"
    sha256sum "$MSI" | awk -v p="$PKG_DIR/" '{ sub(p,"deps/packages/",$2); printf "%s  %s\n", $1, $2 }'
} > "$PKG_DIR/MANIFEST.txt"

log "✓ done"
log ""
log "Next: install on the Windows host. From WSL2:"
log "  bash $SKILL_ROOT/deps/install_windows.sh"
log "Or manually:"
log "  msiexec /i \"\$(wslpath -w $MSI)\" /passive"
