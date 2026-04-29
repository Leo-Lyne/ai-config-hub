#!/usr/bin/env bash
# Populate deps/packages/ with everything needed to bootstrap a fresh VM offline:
#   - VirtualBox installer (Win)
#   - VirtualBox Extension Pack (matching VBox version)
#   - Ubuntu cloud image (jammy, ~700 MB)
#
# Run once on an online machine, then commit deps/packages/ to your skill repo.
# Subsequent users (online or offline) get instant bootstrap via setup_vbox.sh.
#
# Bandwidth: ~840 MB total. All in deps/packages/.
# Override versions/URLs via env vars (defaults are pinned LTS-stable).

set -eu

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"

PKG_DIR="$VBLD_SKILL_ROOT/deps/packages"
mkdir -p "$PKG_DIR"

VBOX_VER="${VBLD_VBOX_VER:-7.2.8}"
VBOX_INSTALLER_URL="${VBLD_VBOX_INSTALLER_URL:-https://download.virtualbox.org/virtualbox/${VBOX_VER}/VirtualBox-${VBOX_VER}-173730-Win.exe}"
VBOX_EXTPACK_URL="${VBLD_VBOX_EXTPACK_URL:-https://download.virtualbox.org/virtualbox/${VBOX_VER}/Oracle_VirtualBox_Extension_Pack-${VBOX_VER}.vbox-extpack}"
CLOUD_IMG_URL="${VBLD_CLOUD_IMG_URL:-https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img}"

VBOX_INSTALLER="$PKG_DIR/VirtualBox-${VBOX_VER}-Win.exe"
VBOX_EXTPACK="$PKG_DIR/Oracle_VirtualBox_Extension_Pack-${VBOX_VER}.vbox-extpack"
CLOUD_IMG="$PKG_DIR/jammy-server-cloudimg-amd64.img"

fetch() {
    local url="$1" dst="$2" desc="$3"
    if [ -f "$dst" ] && [ "$(stat -c%s "$dst" 2>/dev/null || echo 0)" -gt 100000 ]; then
        log "✓ $desc already in deps/packages/ ($(du -h "$dst" | cut -f1))"
        return 0
    fi
    log "downloading $desc"
    log "  URL: $url"
    log "  dst: $dst"
    curl -fL --progress-bar -o "$dst.partial" "$url" || die "download failed: $url"
    mv "$dst.partial" "$dst"
    log "  done: $(du -h "$dst" | cut -f1)"
}

write_manifest() {
    local manifest="$PKG_DIR/MANIFEST.txt"
    {
        echo "# vbox-linux-debug deps/packages/ — bundled at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "VBOX_VER=$VBOX_VER"
        echo
        echo "# SHA256 of bundled artifacts:"
        for f in "$VBOX_INSTALLER" "$VBOX_EXTPACK" "$CLOUD_IMG"; do
            [ -f "$f" ] && sha256sum "$f" | awk -v p="$PKG_DIR/" '{ sub(p,"deps/packages/",$2); printf "%s  %s\n", $1, $2 }'
        done
    } > "$manifest"
    log "wrote $manifest"
}

main() {
    log "=== vbox-linux-debug / fetch_deps ==="
    log "destination: $PKG_DIR"
    log ""
    fetch "$VBOX_INSTALLER_URL" "$VBOX_INSTALLER" "VirtualBox installer (Windows)"
    fetch "$VBOX_EXTPACK_URL"   "$VBOX_EXTPACK"   "VirtualBox Extension Pack v${VBOX_VER}"
    fetch "$CLOUD_IMG_URL"      "$CLOUD_IMG"      "Ubuntu jammy cloud image (~700 MB)"
    write_manifest
    log ""
    log "=== fetch complete ==="
    log "bundled total: $(du -sh "$PKG_DIR" | cut -f1)"
    log "Now: bash $VBLD_SKILL_ROOT/scripts/setup.sh"
}

main "$@"
