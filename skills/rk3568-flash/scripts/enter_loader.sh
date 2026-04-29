#!/usr/bin/env bash
# Put device into Loader (or Maskrom) mode.
# Strategy:
#   - If `adb get-state` returns "device", run `adb reboot loader`.
#   - Otherwise prompt the user to enter Maskrom by holding UPDATE + power.
# Exits 0 once an RK Rockusb device (VID 2207) is visible to Windows
# (caller is responsible for the WSL-side usbip attach — see usb_attach.sh).
set -euo pipefail

ROCKUSB_VID="2207"
ADB_TIMEOUT=5
WAIT_AFTER_REBOOT=8   # seconds

log() { printf '[enter_loader] %s\n' "$*"; }

# Detect adb state quickly (don't hang).
adb_online() {
    timeout "$ADB_TIMEOUT" adb get-state 2>/dev/null | grep -q '^device$'
}

if adb_online; then
    log "adb device online — issuing 'adb reboot loader'"
    adb reboot loader || {
        log "adb reboot loader failed — falling through to manual"
    }
    sleep "$WAIT_AFTER_REBOOT"
else
    log "adb is offline."
    log "MANUAL STEP: hold the UPDATE button on the board, then re-power it."
    log "Press <Enter> once the board is in Maskrom mode (Rockusb enumerates on Windows)."
    read -r
fi

# Sanity: nothing to verify here on the WSL side because the rockusb
# device may not be attached to WSL yet. Just succeed; usb_attach.sh
# will time out itself if it never appears.
log "Loader-entry phase done."
