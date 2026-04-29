#!/usr/bin/env bash
# Helpers shared across transports. Sourced after lib/config.sh.

# Parse parameter.txt to extract partition list (skipping userdata).
list_partitions_from_param() {
    local param="$1"
    [ -f "$param" ] || die "missing parameter file: $param"
    python3 - "$param" <<'PY'
import sys, re
src = open(sys.argv[1]).read()
# CMDLINE: ... mtdparts=...:size@offset(name1),size@offset(name2),...
m = re.search(r'mtdparts=[^:]+:(.*?)(?:\s|$)', src)
if not m:
    sys.exit("no mtdparts in parameter.txt")
parts = re.findall(r'\(([^)]+)\)', m.group(1))
for p in parts:
    if p == 'userdata':
        continue
    print(p)
PY
}

# Image filename for a given partition name. Convention: <part>.img
img_for_part() { printf '%s.img' "$1"; }

# Check device serial via host adb (returns "true" if any device in 'device' state).
host_adb_has_device() {
    [ -n "${RK_ADB_HOST:-}" ] && [ -x "$RK_ADB_HOST" ] || return 1
    "$RK_ADB_HOST" devices 2>/dev/null | awk 'NR>1 && $2=="device" {found=1} END {exit !found}'
}

# Check device via Windows-side RKTools adb.exe (sees devices Windows holds —
# the common case for the windows transport since the Rockchip ADB driver
# lives on Windows, not in WSL).
win_adb_has_device() {
    [ -n "${RK_ADB_WIN_EXE:-}" ] && [ -x "$RK_ADB_WIN_EXE" ] || return 1
    "$RK_ADB_WIN_EXE" devices 2>/dev/null | awk 'NR>1 && $2=="device" {found=1} END {exit !found}'
}

# Probe upgrade_tool LD for a Rockusb device (Loader/MaskRom on Windows side).
# Returns 0 if at least one device shows up.
utool_has_device() {
    [ -n "${RK_UTOOL_EXE:-}" ] && [ -x "$RK_UTOOL_EXE" ] || return 1
    "$RK_UTOOL_EXE" LD 2>&1 | grep -q 'DevNo='
}

# Best-effort transition into Loader mode for the WINDOWS transport.
# Order:
#   1. upgrade_tool LD — already in Loader/MaskRom?  done.
#   2. Windows-side adb (RKTools/windows/adb_fastboot/adb.exe) — adb reboot loader.
#   3. WSL-side adb (covers usbip-attached or network adb).
#   4. Manual MaskRom prompt as last resort.
# Returns 0 once utool_has_device() succeeds, else dies with actionable msg.
ensure_loader_for_windows() {
    local wait_after_reboot=10 deadline_after_reboot=25 step

    # Already in Loader/MaskRom?
    if utool_has_device; then
        log "device already in Loader/MaskRom (upgrade_tool LD sees it)"
        return 0
    fi

    # Try Windows-side adb first — this is where the RK board lives in
    # the windows transport.
    if win_adb_has_device; then
        log "Windows-side adb sees the device → 'adb reboot loader'"
        "$RK_ADB_WIN_EXE" reboot loader || log "adb reboot loader returned non-zero (continuing)"
    elif host_adb_has_device; then
        log "WSL-side adb sees the device → 'adb reboot loader'"
        "$RK_ADB_HOST" reboot loader || log "adb reboot loader returned non-zero (continuing)"
    else
        log "no live adb device found (Windows-side: $([ -n "${RK_ADB_WIN_EXE:-}" ] && echo present || echo missing); WSL: $([ -n "${RK_ADB_HOST:-}" ] && echo present || echo missing))"
        log "MANUAL: hold UPDATE on the board, press RESET (or power-cycle while still holding UPDATE)."
        printf '[rk3568-flash] press <Enter> once the Rockusb device enumerates on Windows... ' >&2
        read -r _
        utool_has_device && return 0
        die "still no Rockusb device after manual MaskRom prompt — check cable / driver."
    fi

    # Poll upgrade_tool LD until device re-appears in Loader (or we time out).
    log "waiting up to ${deadline_after_reboot}s for Rockusb (Loader) to enumerate..."
    sleep "$wait_after_reboot"
    for step in $(seq 1 $((deadline_after_reboot - wait_after_reboot))); do
        utool_has_device && { log "Loader is up after $((wait_after_reboot + step - 1))s"; return 0; }
        sleep 1
    done
    die "device did not re-enumerate as Rockusb within ${deadline_after_reboot}s after 'adb reboot loader'.
  Common causes:
    - Board hung after reboot — try MaskRom instead (UPDATE + RESET).
    - Rockchip USB driver missing on Windows — install from RKTools/windows/DriverAssitant_v5.1.1.zip.
    - Cable is charge-only or USB-A end is broken."
}
