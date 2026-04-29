#!/usr/bin/env bash
# USB filter management for the VM.
# Usage:
#   usb list
#   usb add  <vid>:<pid> [name]
#   usb rm   <name>

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"
vbm_present || die "VirtualBox not installed."
vm_exists   || die "VM '$VBLD_VM_NAME' not found."

action="${1:-list}"
shift || true

case "$action" in
    list)
        "$VBLD_VBOX_BIN" showvminfo "$VBLD_VM_NAME" --machinereadable 2>/dev/null \
            | awk -F= '
                /^USBFilterName/     { gsub(/"/,"",$2); name=$2 }
                /^USBFilterActive/   { gsub(/"/,"",$2); act=$2 }
                /^USBFilterVendorId/ { gsub(/"/,"",$2); vid=$2 }
                /^USBFilterProductId/{ gsub(/"/,"",$2); pid=$2;
                                       printf "  %-20s VID=%s PID=%s active=%s\n", name, vid, pid, act }'
        ;;
    add)
        spec="${1:?Usage: usb add VID:PID [name]}"
        vid="${spec%:*}"; pid="${spec#*:}"
        name="${2:-usb-${vid}-${pid}}"
        idx=$("$VBLD_VBOX_BIN" showvminfo "$VBLD_VM_NAME" --machinereadable 2>/dev/null \
              | grep -c '^USBFilterName')
        "$VBLD_VBOX_BIN" usbfilter add "$idx" --target "$VBLD_VM_NAME" \
            --name "$name" --vendorid "$vid" --productid "$pid" --active yes
        log "Added filter '$name' VID=$vid PID=$pid (index $idx)"
        ;;
    rm)
        name="${1:?Usage: usb rm <name>}"
        fidx=$("$VBLD_VBOX_BIN" showvminfo "$VBLD_VM_NAME" --machinereadable 2>/dev/null \
              | awk -F= -v want="\"$name\"" '
                    /^USBFilterName/ { if ($2==want) { print n; exit } else n++ }')
        [ -n "$fidx" ] || die "Filter '$name' not found."
        "$VBLD_VBOX_BIN" usbfilter remove "$fidx" --target "$VBLD_VM_NAME"
        log "Removed filter '$name' (index $fidx)"
        ;;
    *)
        die "Unknown subcommand: $action  (list | add | rm)"
        ;;
esac
