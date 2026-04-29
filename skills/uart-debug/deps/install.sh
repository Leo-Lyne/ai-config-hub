#!/usr/bin/env bash
# Build deps/.venv with pyserial installed from deps/wheels/ (offline-capable).
# Idempotent — re-runs only re-pip-install if the venv is missing or wheels changed.

set -eu

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$SKILL_ROOT/deps/.venv"
WHEEL_DIR="$SKILL_ROOT/deps/wheels"

log() { printf '[uart-debug/install] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

have python3 || die "need python3"

# If wheels missing, try to fetch them first.
if ! ls "$WHEEL_DIR"/pyserial-*.whl >/dev/null 2>&1; then
    log "no wheels in $WHEEL_DIR — running fetch_deps.sh first"
    bash "$SKILL_ROOT/deps/fetch_deps.sh"
fi

if [ ! -x "$VENV/bin/python" ]; then
    log "creating $VENV"
    python3 -m venv "$VENV"
fi

log "installing pyserial from $WHEEL_DIR"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet --no-index --find-links "$WHEEL_DIR" pyserial

log "✓ ready: $VENV/bin/python -c 'import serial; print(serial.__version__)' = $("$VENV/bin/python" -c 'import serial; print(serial.__version__)')"
