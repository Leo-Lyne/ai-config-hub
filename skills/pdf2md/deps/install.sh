#!/usr/bin/env bash
# Build deps/.venv from deps/wheels/ + deps/requirements.txt.
# Use this on a fresh machine to recreate the venv from the bundled wheels
# (offline-capable). Idempotent.

set -eu

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$SKILL_ROOT/deps/.venv"
WHEEL_DIR="$SKILL_ROOT/deps/wheels"
REQS="${PDF2MD_REQS_FILE:-$SKILL_ROOT/deps/requirements.txt}"

log() { printf '[pdf2md/install] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

have python3 || die "need python3"
[ -f "$REQS" ] || die "missing $REQS"

# If wheels missing, fetch first.
if ! ls "$WHEEL_DIR"/*.whl >/dev/null 2>&1; then
    log "no wheels in $WHEEL_DIR — running fetch_deps.sh first"
    bash "$SKILL_ROOT/deps/fetch_deps.sh"
fi

if [ ! -x "$VENV/bin/python" ]; then
    log "creating $VENV"
    python3 -m venv "$VENV"
fi

log "installing from $WHEEL_DIR (offline)"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install \
    --quiet \
    --no-index \
    --find-links "$WHEEL_DIR" \
    --requirement "$REQS"

log "✓ installed marker-pdf $("$VENV/bin/python" -c 'import marker; print(getattr(marker, "__version__", "?"))')"
log "  invoke via: $VENV/bin/python $SKILL_ROOT/scripts/pdf2md.py [pdf...]"
