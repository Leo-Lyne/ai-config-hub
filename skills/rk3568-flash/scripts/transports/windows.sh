#!/usr/bin/env bash
# Windows transport: invoke upgrade_tool.exe natively from WSL via Windows interop.
# Used by flash.sh dispatcher; defines transport_full / parts / auto / setup / status.

. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

_stage_to_win_cache() {
    mkdir -p "$RK_WIN_CACHE_LX"
    local src="$1" dst_basename="${2:-$(basename "$1")}"
    cp "$src" "$RK_WIN_CACHE_LX/$dst_basename"
    printf '%s\\%s\n' "$RK_WIN_CACHE_W" "$dst_basename"  # Windows path for upgrade_tool.exe
}

_utool() { "$RK_UTOOL_EXE" "$@"; }

transport_status() {
    print_config_summary
    if [ -n "${RK_ADB_WIN_EXE:-}" ] && [ -x "$RK_ADB_WIN_EXE" ]; then
        printf '\n[windows] RKTools adb.exe sees:\n' >&2
        "$RK_ADB_WIN_EXE" devices 2>&1 | sed 's/^/  /' >&2
    fi
    if [ -n "${RK_ADB_HOST:-}" ] && [ -x "$RK_ADB_HOST" ]; then
        printf '\n[wsl] adb sees:\n' >&2
        "$RK_ADB_HOST" devices 2>&1 | sed 's/^/  /' >&2
    fi
    require_utool_exe 2>/dev/null && {
        printf '\n[windows] upgrade_tool.exe sees:\n' >&2
        _utool LD 2>&1 | grep -E 'DevNo|connected' | sed 's/^/  /' >&2 || true
    }
}

transport_setup() {
    log "windows transport requires no provisioning."
    log "Just plug the board in via USB and put it in Loader (V+) or Maskrom (UPDATE)."
    require_utool_exe
}

transport_full() {
    require_utool_exe
    local DIR="${1:-${RK_IMAGE_DIR:-}}"
    [ -n "$DIR" ] || { require_image_dir; DIR="$RK_IMAGE_DIR"; }

    # Single update.img mode
    if [ -f "$DIR" ] && [ "${DIR##*.}" = "img" ]; then
        log "single update.img mode: $DIR"
        ensure_loader_for_windows
        local winpath; winpath="$(_stage_to_win_cache "$DIR" update.img)"
        _utool UF "$winpath" || die "UF failed"
        log "full flash done (update.img)"
        return 0
    fi

    [ -d "$DIR" ] || die "not a directory: $DIR"
    local PARAM="$DIR/parameter.txt" LOADER="$DIR/MiniLoaderAll.bin"
    [ -f "$PARAM"  ] || die "missing $PARAM"
    [ -f "$LOADER" ] || die "missing $LOADER"

    local PARTS; PARTS="$(list_partitions_from_param "$PARAM")"
    log "partitions to flash: $(echo "$PARTS" | tr '\n' ' ')"

    log "stage loader + parameter + partition images → $RK_WIN_CACHE_LX"
    cp "$LOADER" "$RK_WIN_CACHE_LX/MiniLoaderAll.bin"
    cp "$PARAM"  "$RK_WIN_CACHE_LX/parameter.txt"
    while IFS= read -r p; do
        local img="$DIR/${p}.img"
        if [ ! -s "$img" ]; then log "skip $p (no image)"; continue; fi
        cp "$img" "$RK_WIN_CACHE_LX/${p}.img"
    done <<< "$PARTS"

    ensure_loader_for_windows

    log "UL MiniLoaderAll.bin"
    _utool UL "${RK_WIN_CACHE_W}\\MiniLoaderAll.bin" -noreset || die "UL failed"
    log "DI -p parameter.txt"
    _utool DI -p "${RK_WIN_CACHE_W}\\parameter.txt" || die "DI -p failed"

    while IFS= read -r p; do
        [ -s "$RK_WIN_CACHE_LX/${p}.img" ] || continue
        log "DI -$p"
        _utool DI -"$p" "${RK_WIN_CACHE_W}\\${p}.img" 2>&1 \
            | grep -E 'Download|ERROR|Fail' | tail -3 \
            || die "DI -$p failed"
    done <<< "$PARTS"

    log "RD"
    _utool RD || true
    log "full flash done."
}

transport_parts() {
    require_utool_exe
    require_rockdev_dir
    [ "$#" -gt 0 ] || die "usage: parts <p1> [p2]..."

    # userdata extra confirmation (data-loss).
    for p in "$@"; do
        if [ "$p" = "userdata" ]; then
            log "WARNING: 'userdata' flash will erase all user data."
            printf "Type YES to continue: " >&2
            read -r ans
            [ "$ans" = "YES" ] || die "user aborted"
        fi
    done

    # Validate before touching device
    for p in "$@"; do
        local img="$RK_ROCKDEV_DIR/${p}.img"
        [ -s "$img" ] || die "missing or empty image: $img"
    done

    mkdir -p "$RK_WIN_CACHE_LX"
    for p in "$@"; do
        log "stage  $p ← $RK_ROCKDEV_DIR/${p}.img"
        cp "$RK_ROCKDEV_DIR/${p}.img" "$RK_WIN_CACHE_LX/${p}.img"
    done

    ensure_loader_for_windows

    for p in "$@"; do
        log "DI -$p"
        _utool DI -"$p" "${RK_WIN_CACHE_W}\\${p}.img" 2>&1 \
            | grep -E 'Download|ERROR|Fail' | tail -3 \
            || die "DI -$p failed"
    done

    log "parts flash done: $*"
}

transport_auto() {
    require_utool_exe
    require_rockdev_dir
    python3 "$RK_SKILL_ROOT/scripts/flash_auto.py" \
        --rockdev-dir "$RK_ROCKDEV_DIR" \
        --state-file "$RK_STATE_DIR/last_flash.json" \
        --transport-script "$0" \
        windows
}
