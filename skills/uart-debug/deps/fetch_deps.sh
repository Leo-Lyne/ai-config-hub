#!/usr/bin/env bash
# Bundle pyserial wheel(s) into deps/wheels/ for offline-installable venv.
# Idempotent — re-running re-uses existing wheels.
#
# Override the pyserial version via $UART_PYSERIAL_VER (default: pinned LTS).

set -eu

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHEEL_DIR="$SKILL_ROOT/deps/wheels"
PKG_DIR="$SKILL_ROOT/deps/packages"
mkdir -p "$WHEEL_DIR" "$PKG_DIR"

PYSERIAL_VER="${UART_PYSERIAL_VER:-3.5}"

log() { printf '[uart-debug/fetch_deps] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

have python3 || die "need python3"

# pip download is the cleanest way to grab a wheel + its (zero, in pyserial's case) deps.
log "downloading pyserial==$PYSERIAL_VER → $WHEEL_DIR/"
python3 -m pip download \
    --quiet --no-deps --dest "$WHEEL_DIR" \
    "pyserial==$PYSERIAL_VER" \
    || die "pip download failed (need network or a configured index)"

ls -lh "$WHEEL_DIR"/pyserial-*.whl 2>/dev/null | sed 's/^/  /'

# manifest
{
    echo "# uart-debug deps/ — bundled $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "PYSERIAL_VER=$PYSERIAL_VER"
    echo
    for f in "$WHEEL_DIR"/*.whl; do
        [ -f "$f" ] && sha256sum "$f" | awk -v p="$WHEEL_DIR/" '{ sub(p,"deps/wheels/",$2); printf "%s  %s\n", $1, $2 }'
    done
} > "$PKG_DIR/MANIFEST.txt"

log "✓ done. Now: bash $SKILL_ROOT/deps/install.sh"
