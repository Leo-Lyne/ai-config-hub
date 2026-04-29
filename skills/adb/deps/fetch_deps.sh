#!/usr/bin/env bash
# Bundle Android platform-tools (adb + fastboot + friends) into deps/platform-tools/.
# Idempotent — re-running re-uses the existing extract.
#
# Override URL via $ADB_PLATFORM_TOOLS_URL (default: Google's official Linux build).
# OS auto-detected: linux | darwin | windows.

set -eu

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_DIR="$SKILL_ROOT/deps/packages"
DEST="$SKILL_ROOT/deps/platform-tools"
mkdir -p "$PKG_DIR"

log() { printf '[adb/fetch_deps] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Detect OS for the right download bundle
OS="linux"
case "$(uname -s)" in
    Linux*)   OS="linux" ;;
    Darwin*)  OS="darwin" ;;
    MINGW*|CYGWIN*|MSYS*) OS="windows" ;;
esac
# WSL2: still use linux build (we run inside Linux)
grep -qi 'microsoft\|wsl' /proc/version 2>/dev/null && OS="linux"

URL="${ADB_PLATFORM_TOOLS_URL:-https://dl.google.com/android/repository/platform-tools-latest-${OS}.zip}"
ZIP="$PKG_DIR/platform-tools-latest-${OS}.zip"

if [ -x "$DEST/adb" ]; then
    log "✓ already bundled: $DEST/adb"
    "$DEST/adb" --version | head -1 | sed 's/^/  /'
    exit 0
fi

if [ ! -f "$ZIP" ] || [ "$(stat -c%s "$ZIP" 2>/dev/null || echo 0)" -lt 1000000 ]; then
    log "downloading platform-tools (${OS})"
    log "  URL: $URL"
    log "  dst: $ZIP"
    have curl || die "need curl"
    curl -fL --progress-bar -o "$ZIP.partial" "$URL" || die "download failed"
    mv "$ZIP.partial" "$ZIP"
fi

have unzip || die "need unzip (apt install unzip)"

log "extracting → $DEST..."
TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT
unzip -q "$ZIP" -d "$TMPD"
rm -rf "$DEST"
mv "$TMPD/platform-tools" "$DEST"
chmod +x "$DEST"/adb "$DEST"/fastboot 2>/dev/null || true

log "✓ done: $("$DEST/adb" --version | head -1)"
log "  bundled at: $DEST"

# manifest
{
    echo "# adb skill — platform-tools bundle"
    echo "# generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "OS=$OS"
    echo "URL=$URL"
    echo "SHA256:"
    sha256sum "$ZIP" | awk -v p="$PKG_DIR/" '{ sub(p,"deps/packages/",$2); printf "%s  %s\n", $1, $2 }'
} > "$PKG_DIR/MANIFEST.txt"
