#!/usr/bin/env bash
# vbox-linux transport: flash via Linux upgrade_tool v2.4 inside a headless
# VirtualBox VM that owns the USB device via VBox's USB filter.
#
# This bypasses Windows USB driver entirely — useful when:
#   - Windows-side upgrade_tool.exe is unavailable / broken
#   - You want a reproducible Linux flashing env (e.g. CI / fresh laptop)
#   - You're already in a Linux dev workflow and don't want WSL→Windows interop

. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

# ─────────────────────────────────────────────────────────────────────────────
# VM-internal helpers
# ─────────────────────────────────────────────────────────────────────────────
SSH_OPTS=(-p "$RK_VBOX_SSH_PORT" -i "$RK_VBOX_SSH_KEY" \
          -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
          -o ConnectTimeout=8 -o BatchMode=yes)
SSH_TARGET="${RK_VBOX_SSH_USER}@${RK_VBOX_SSH_HOST}"

ssh_vm()      { ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "$@"; }
scp_to_vm()   { scp -P "$RK_VBOX_SSH_PORT" -i "$RK_VBOX_SSH_KEY" \
                    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                    -q "$@" "${SSH_TARGET}:"; }
ssh_ready()   { ssh "${SSH_OPTS[@]}" -o ConnectTimeout=4 "$SSH_TARGET" 'true' 2>/dev/null; }

vbm()                  { "$RK_VBOX_BIN" "$@"; }
vbox_vm_exists()       { vbm list vms 2>/dev/null | grep -q "\"$RK_VBOX_VM_NAME\""; }
vbox_vm_running()      { vbm list runningvms 2>/dev/null | grep -q "\"$RK_VBOX_VM_NAME\""; }
vbox_extpack_present() { vbm list extpacks 2>/dev/null | grep -q '^Pack no\.'; }

vbox_filter_count() {
    vbm showvminfo "$RK_VBOX_VM_NAME" --machinereadable 2>/dev/null \
        | grep -c '^USBFilterName'
}

vbox_ensure_filters() {
    local n; n="$(vbox_filter_count)"
    if [ "$n" -lt 3 ]; then
        log "Adding 3 USB filters for VID 2207 (Loader 350a / Maskrom 350b / ADB 0006)..."
        vbm usbfilter add 0 --target "$RK_VBOX_VM_NAME" --name rockusb-loader  --vendorid 2207 --productid 350a --active yes 2>/dev/null || true
        vbm usbfilter add 1 --target "$RK_VBOX_VM_NAME" --name rockusb-maskrom --vendorid 2207 --productid 350b --active yes 2>/dev/null || true
        vbm usbfilter add 2 --target "$RK_VBOX_VM_NAME" --name rockchip-adb    --vendorid 2207 --productid 0006 --active yes 2>/dev/null || true
    fi
    for i in 0 1 2; do
        vbm usbfilter modify "$i" --target "$RK_VBOX_VM_NAME" --active yes 2>/dev/null || true
    done
}

vbox_wait_ssh() {
    local timeout="${1:-300}" t=0
    log "waiting up to ${timeout}s for SSH on ${RK_VBOX_SSH_HOST}:${RK_VBOX_SSH_PORT}..."
    while ! ssh_ready; do
        sleep 6; t=$((t + 6))
        [ "$t" -ge "$timeout" ] && die "SSH not ready after ${timeout}s"
    done
}

vbox_ensure_running() {
    require_vbox
    if ! vbox_vm_exists; then
        die "VM '$RK_VBOX_VM_NAME' not found. Run \`/rk3568-flash vbox-linux setup\` first."
    fi
    if ! vbox_vm_running; then
        log "starting VM '$RK_VBOX_VM_NAME' headless..."
        vbm startvm "$RK_VBOX_VM_NAME" --type headless | tail -1
        vbox_wait_ssh 300
    elif ! ssh_ready; then
        vbox_wait_ssh 60
    fi
}

vbox_ensure_tool_in_vm() {
    if ssh_vm "test -x $RK_VBOX_UTOOL_VM" 2>/dev/null; then return 0; fi
    require_utool_linux
    log "pushing v2.4 upgrade_tool → VM:$RK_VBOX_UTOOL_VM (one-time)..."
    scp -P "$RK_VBOX_SSH_PORT" -i "$RK_VBOX_SSH_KEY" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -q \
        "$RK_UTOOL_LINUX" "${SSH_TARGET}:${RK_VBOX_UTOOL_VM}"
    ssh_vm "chmod +x $RK_VBOX_UTOOL_VM"
}

# Bring device into Loader from inside the VM (works regardless of starting state).
vbox_in_loader()  { ssh_vm 'lsusb | grep -q "ID 2207:350a"' 2>/dev/null; }
vbox_in_adb()     { ssh_vm 'lsusb | grep -q "ID 2207:0006"' 2>/dev/null; }
vbox_in_maskrom() { ssh_vm 'lsusb | grep -q "ID 2207:350b"' 2>/dev/null; }

vbox_ensure_loader() {
    vbox_in_loader && return 0
    if vbox_in_adb; then
        log "device in Android — adb reboot loader (inside VM)..."
        ssh_vm 'adb reboot loader' || true
    elif vbox_in_maskrom; then
        log "device in MaskRom — DB will load it via UL step"
        return 0  # UL handles MaskRom→Loader
    else
        log "device not visible in VM. Plug it in, or hold V+ for Loader / UPDATE for MaskRom."
    fi
    log "waiting up to 60s for Loader (PID 2207:350a) inside VM..."
    for _ in $(seq 1 30); do
        vbox_in_loader && return 0
        sleep 2
    done
    die "Loader (or MaskRom) never appeared. Run \`status\` for diagnostics."
}

# Stage host files into VM at /tmp/rk3568-flash/<basename>
vbox_stage() {
    ssh_vm 'mkdir -p /tmp/rk3568-flash'
    scp -P "$RK_VBOX_SSH_PORT" -i "$RK_VBOX_SSH_KEY" \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -q \
        "$@" "${SSH_TARGET}:/tmp/rk3568-flash/"
}

# ─────────────────────────────────────────────────────────────────────────────
# Public transport interface
# ─────────────────────────────────────────────────────────────────────────────

transport_status() {
    print_config_summary
    require_vbox
    printf '\n[vbox-linux] VM state:\n' >&2
    if vbox_vm_exists; then
        if vbox_vm_running; then printf '  %s: RUNNING\n' "$RK_VBOX_VM_NAME"
        else printf '  %s: stopped\n' "$RK_VBOX_VM_NAME"; fi
    else
        printf '  %s: not created — run \`setup\`\n' "$RK_VBOX_VM_NAME"
    fi
    printf '\n[vbox-linux] USB filters:\n' >&2
    vbm showvminfo "$RK_VBOX_VM_NAME" --machinereadable 2>/dev/null \
        | awk -F= '
            /^USBFilterName/     { gsub(/"/,"",$2); name=$2 }
            /^USBFilterActive/   { gsub(/"/,"",$2); act=$2 }
            /^USBFilterVendorId/ { gsub(/"/,"",$2); vid=$2 }
            /^USBFilterProductId/{ gsub(/"/,"",$2); pid=$2;
                                   printf "  %-20s VID=%s PID=%s active=%s\n", name, vid, pid, act }' \
        || printf '  (no filters / VM not configured)\n'

    if vbox_vm_running && ssh_ready; then
        printf '\n[vbox-linux] device inside VM:\n' >&2
        ssh_vm 'lsusb | grep 2207 || echo "  (no Rockchip device visible to VM)"'
    fi
}

transport_setup() {
    log "delegating provisioning to deps/setup_vbox.sh..."
    bash "$RK_SKILL_ROOT/deps/setup_vbox.sh" "$@"
}

transport_full() {
    require_vbox
    require_utool_linux
    local DIR="${1:-${RK_IMAGE_DIR:-}}"
    [ -n "$DIR" ] || { require_image_dir; DIR="$RK_IMAGE_DIR"; }
    [ -d "$DIR" ] || die "not a directory: $DIR"
    local PARAM="$DIR/parameter.txt" LOADER="$DIR/MiniLoaderAll.bin"
    [ -f "$PARAM"  ] || die "missing $PARAM"
    [ -f "$LOADER" ] || die "missing $LOADER"

    vbox_ensure_running
    vbox_ensure_filters
    vbox_ensure_tool_in_vm
    vbox_ensure_loader

    log "device: $(ssh_vm 'lsusb | grep 2207')"

    local PARTS; PARTS="$(list_partitions_from_param "$PARAM")"
    log "partitions: $(echo "$PARTS" | tr '\n' ' ')"

    log "scp images → VM /tmp/rk3568-flash/..."
    local files=("$LOADER" "$PARAM")
    while IFS= read -r p; do
        local img="$DIR/${p}.img"
        [ -s "$img" ] && files+=("$img")
    done <<< "$PARTS"
    vbox_stage "${files[@]}"

    log "UL MiniLoaderAll.bin -noreset"
    ssh_vm "$RK_VBOX_UTOOL_VM UL /tmp/rk3568-flash/MiniLoaderAll.bin -noreset 2>&1 | tail -3" \
        | grep -E 'Upgrade|fail|FAIL|error' || die "UL failed"

    log "DI -p parameter.txt"
    ssh_vm "$RK_VBOX_UTOOL_VM DI -p /tmp/rk3568-flash/parameter.txt 2>&1 | tr '\\r' '\\n' | grep -vE '\\([0-9]+%\\)\$' | tail -3"

    while IFS= read -r p; do
        ssh_vm "test -s /tmp/rk3568-flash/${p}.img" || { log "skip $p (not staged)"; continue; }
        log "DI -$p"
        ssh_vm "$RK_VBOX_UTOOL_VM DI -$p /tmp/rk3568-flash/${p}.img 2>&1 | tr '\\r' '\\n' | grep -vE '\\([0-9]+%\\)\$' | tail -3" \
            || die "DI -$p failed"
    done <<< "$PARTS"

    # Wipe userdata first 128 MiB so Android rebuilds the ext4 filesystem on first boot.
    # (Required when previous image was a different Android version / userdata layout
    # changed; otherwise harmless — same as factory reset.)
    log "EL userdata first 128 MiB (clears any stale ext4 superblock)"
    # Find userdata LBA from parameter.txt
    local UDATA_LBA; UDATA_LBA="$(python3 - "$PARAM" <<'PY'
import sys, re
src = open(sys.argv[1]).read()
m = re.search(r'mtdparts=[^:]+:(.*?)(?:\s|$)', src)
if not m: sys.exit("no mtdparts")
# Each entry: size@offset(name)
pos = 0
for entry in m.group(1).split(','):
    em = re.match(r'(0x[0-9a-fA-F]+|\-)@(0x[0-9a-fA-F]+)\(([^)]+)\)', entry)
    if em and em.group(3) == 'userdata':
        print(em.group(2))
        break
PY
)"
    if [ -n "$UDATA_LBA" ]; then
        ssh_vm "$RK_VBOX_UTOOL_VM EL $UDATA_LBA 0x40000 2>&1 | tail -2" || true
    else
        log "  (couldn't resolve userdata LBA — skipping EL)"
    fi

    log "RD"
    ssh_vm "$RK_VBOX_UTOOL_VM RD" || true
    log "full flash done. Android first boot takes ~45-60s (rebuilds /data)."
}

transport_parts() {
    require_vbox
    require_utool_linux
    require_rockdev_dir
    [ "$#" -gt 0 ] || die "usage: parts <p1> [p2]..."

    for p in "$@"; do
        if [ "$p" = "userdata" ]; then
            log "WARNING: 'userdata' flash will erase all user data."
            printf "Type YES to continue: " >&2
            read -r ans
            [ "$ans" = "YES" ] || die "user aborted"
        fi
        local img="$RK_ROCKDEV_DIR/${p}.img"
        [ -s "$img" ] || die "missing or empty image: $img"
    done

    vbox_ensure_running
    vbox_ensure_filters
    vbox_ensure_tool_in_vm
    vbox_ensure_loader

    log "device: $(ssh_vm 'lsusb | grep 2207')"

    # Loader must be uploaded once before any DI to avoid the in-flash Loader's
    # tiny RAM buffer truncating large writes.
    local LOADER="$RK_ROCKDEV_DIR/MiniLoaderAll.bin"
    [ -f "$LOADER" ] || LOADER="${RK_IMAGE_DIR:-}/MiniLoaderAll.bin"
    [ -f "$LOADER" ] || die "MiniLoaderAll.bin not found in $RK_ROCKDEV_DIR or $RK_IMAGE_DIR"

    log "scp loader + ${#} images → VM..."
    local files=("$LOADER")
    for p in "$@"; do files+=("$RK_ROCKDEV_DIR/${p}.img"); done
    vbox_stage "${files[@]}"

    log "UL MiniLoaderAll.bin -noreset (refresh Loader to RAM — required for >7MiB writes)"
    ssh_vm "$RK_VBOX_UTOOL_VM UL /tmp/rk3568-flash/MiniLoaderAll.bin -noreset 2>&1 | tail -3"

    for p in "$@"; do
        log "DI -$p"
        ssh_vm "$RK_VBOX_UTOOL_VM DI -$p /tmp/rk3568-flash/${p}.img 2>&1 | tr '\\r' '\\n' | grep -vE '\\([0-9]+%\\)\$' | tail -3" \
            || die "DI -$p failed"
    done

    log "RD"
    ssh_vm "$RK_VBOX_UTOOL_VM RD" || true
    log "parts flash done: $*"
}

transport_auto() {
    require_vbox
    require_utool_linux
    require_rockdev_dir
    python3 "$RK_SKILL_ROOT/scripts/flash_auto.py" \
        --rockdev-dir "$RK_ROCKDEV_DIR" \
        --state-file "$RK_STATE_DIR/last_flash.json" \
        --transport-script "$0" \
        vbox-linux
}
