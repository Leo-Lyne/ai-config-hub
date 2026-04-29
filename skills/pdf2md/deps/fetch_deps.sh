#!/usr/bin/env bash
# Bundle all pdf2md wheels into deps/wheels/ for offline-installable venv.
# Idempotent — re-running re-uses existing wheels (pip skips what's there).
#
# What this fetches: torch + transformers + surya-ocr + marker-pdf and their
# pinned transitive deps from deps/requirements.txt. Total ~3-5 GB on Linux x86_64
# (torch + cuda libs dominate).
#
# Override the requirements file via $PDF2MD_REQS_FILE.

set -eu

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WHEEL_DIR="$SKILL_ROOT/deps/wheels"
REQS="${PDF2MD_REQS_FILE:-$SKILL_ROOT/deps/requirements.txt}"
mkdir -p "$WHEEL_DIR"

log() { printf '[pdf2md/fetch_deps] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

have python3 || die "need python3"
[ -f "$REQS" ] || die "missing $REQS"

log "downloading wheels into $WHEEL_DIR (pinned by $REQS)"
log "  this is ~3-5 GB on Linux x86_64; first run takes a while"

python3 -m pip download \
    --quiet \
    --dest "$WHEEL_DIR" \
    --requirement "$REQS" \
    || die "pip download failed (network? bad pin?)"

log "✓ done. $(ls "$WHEEL_DIR"/*.whl 2>/dev/null | wc -l) wheels in $WHEEL_DIR ($(du -sh "$WHEEL_DIR" | cut -f1))"
log ""
log "Next: bash $SKILL_ROOT/deps/install.sh"
