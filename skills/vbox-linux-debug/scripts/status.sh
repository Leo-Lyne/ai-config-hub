#!/usr/bin/env bash
# Print VM + USB filter + attached-device summary.

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"

print_config_summary
printf '\n'

vbm_present || die "VirtualBox not installed. Run \`setup\`."

printf '== VM ==\n'
if vm_exists; then
    state="STOPPED"; vm_running && state="RUNNING"
    printf '  name: %s   state: %s\n' "$VBLD_VM_NAME" "$state"
    "$VBLD_VBOX_BIN" showvminfo "$VBLD_VM_NAME" --machinereadable 2>/dev/null \
        | grep -E '^(memory|cpus|natnet1|VRDE|usbxhci|GuestOSType|CfgFile)=' \
        | sed 's/^/  /'
else
    printf '  VM "%s" does not exist. Run `setup`.\n' "$VBLD_VM_NAME"
fi

printf '\n== USB filters ==\n'
if vm_exists; then
    "$VBLD_VBOX_BIN" showvminfo "$VBLD_VM_NAME" --machinereadable 2>/dev/null \
        | awk -F= '
            /^USBFilterName/     { gsub(/"/,"",$2); name=$2 }
            /^USBFilterActive/   { gsub(/"/,"",$2); act=$2 }
            /^USBFilterVendorId/ { gsub(/"/,"",$2); vid=$2 }
            /^USBFilterProductId/{ gsub(/"/,"",$2); pid=$2;
                                   printf "  %-20s VID=%s PID=%s active=%s\n", name, vid, pid, act }'
fi

printf '\n== Host USB candidates ==\n'
"$VBLD_VBOX_BIN" list usbhost 2>/dev/null \
    | awk '/UUID:/ { uuid=$2 } /VendorId:/ { vid=$2 } /ProductId:/ { pid=$2 }
           /Product:/ { prod=$0 }
           /Current State:/ { state=$3
                              # show only devices the user asked for via filters,
                              # plus VBox proxy (80ee) so attach state is observable
                              if (vid != "" && pid != "") {
                                printf "  uuid=%s vid=%s pid=%s state=%s\n", uuid, vid, pid, state
                                printf "    %s\n", prod
                              }
                              vid=""; pid=""; prod=""; state="" }'

if vm_running; then
    printf '\n== Attached to VM ==\n'
    "$VBLD_VBOX_BIN" showvminfo "$VBLD_VM_NAME" --machinereadable 2>/dev/null \
        | awk -F= '
            /^USBAttachVendorId/ { vid=$2 }
            /^USBAttachProductId/{ pid=$2 }
            /^USBAttachProduct/  { prod=$2; gsub(/"/,"",vid); gsub(/"/,"",pid); gsub(/"/,"",prod);
                                   printf "  %s:%s — %s\n", vid, pid, prod }'

    printf '\n== SSH ==\n'
    if ssh_ready; then
        printf '  ssh -p %s %s   OK\n' "$VBLD_SSH_PORT" "$SSH_TARGET"
        ssh_vm 'echo "  uname: $(uname -srm)"; echo "  ip: $(ip -br a | awk "/UP/ {print \$1, \$3}")"; echo "  lsusb: $(lsusb | wc -l) devices"' 2>/dev/null
    else
        printf '  ssh -p %s %s   not ready\n' "$VBLD_SSH_PORT" "$SSH_TARGET"
    fi
fi
