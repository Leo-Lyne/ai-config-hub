#!/usr/bin/env bash
# Populate deps/packages/ with everything needed by the vbox-linux transport
# so the skill works offline. Run this once on an online machine, then commit
# the populated deps/packages/ to your skill repo. Subsequent users (online or
# offline) use the bundled artifacts via setup_vbox.sh.
#
# Bandwidth: ~840 MB total (VBox 120MB + extpack 20MB + Ubuntu cloud img 700MB).
# Disk: same. All in deps/packages/.
#
# Override versions/URLs via env vars (defaults are pinned LTS-stable releases).

set -eu

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"

PKG_DIR="$RK_SKILL_ROOT/deps/packages"
mkdir -p "$PKG_DIR"

# Pinned defaults (override via env)
VBOX_VER="${RK_VBOX_VER:-7.2.8}"
VBOX_INSTALLER_URL="${RK_VBOX_INSTALLER_URL:-https://download.virtualbox.org/virtualbox/${VBOX_VER}/VirtualBox-${VBOX_VER}-173730-Win.exe}"
VBOX_EXTPACK_URL="${RK_VBOX_EXTPACK_URL:-https://download.virtualbox.org/virtualbox/${VBOX_VER}/Oracle_VirtualBox_Extension_Pack-${VBOX_VER}.vbox-extpack}"
CLOUD_IMG_URL="${RK_CLOUD_IMG_URL:-https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img}"

VBOX_INSTALLER="${PKG_DIR}/VirtualBox-${VBOX_VER}-Win.exe"
VBOX_EXTPACK="${PKG_DIR}/Oracle_VirtualBox_Extension_Pack-${VBOX_VER}.vbox-extpack"
CLOUD_IMG="${PKG_DIR}/jammy-server-cloudimg-amd64.img"

fetch() {
    local url="$1" dst="$2" desc="$3"
    if [ -f "$dst" ] && [ "$(stat -c%s "$dst" 2>/dev/null || echo 0)" -gt 100000 ]; then
        log "✓ $desc already in deps/packages/ ($(du -h "$dst" | cut -f1))"
        return 0
    fi
    log "downloading $desc → $dst"
    log "  URL: $url"
    curl -fL --progress-bar -o "$dst.partial" "$url" || die "download failed: $url"
    mv "$dst.partial" "$dst"
    log "  done: $(du -h "$dst" | cut -f1)"
}

write_manifest() {
    local manifest="$PKG_DIR/MANIFEST.txt"
    {
        echo "# rk3568-flash deps/packages/ — bundled at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "# Pinned versions (override via env vars in fetch_deps.sh)"
        echo "VBOX_VER=$VBOX_VER"
        echo
        echo "# SHA256 of bundled artifacts:"
        for f in "$VBOX_INSTALLER" "$VBOX_EXTPACK" "$CLOUD_IMG"; do
            [ -f "$f" ] && sha256sum "$f" | awk '{printf "%s  %s\n", $1, "deps/packages/"$2}' | sed "s|$PKG_DIR/||"
        done
    } > "$manifest"
    log "wrote $manifest"
}

main() {
    log "=== rk3568-flash / deps / fetch_deps ==="
    log "destination: $PKG_DIR"
    log ""
    fetch "$VBOX_INSTALLER_URL" "$VBOX_INSTALLER" "VirtualBox installer (Windows)"
    fetch "$VBOX_EXTPACK_URL"   "$VBOX_EXTPACK"   "VirtualBox Extension Pack v${VBOX_VER}"
    fetch "$CLOUD_IMG_URL"      "$CLOUD_IMG"      "Ubuntu jammy cloud image (~700 MB)"
    write_manifest
    log ""
    log "=== fetch complete ==="
    log "bundled total: $(du -sh "$PKG_DIR" | cut -f1)"
    log "Now: bash $RK_SKILL_ROOT/deps/setup_vbox.sh"
}

main "$@"
