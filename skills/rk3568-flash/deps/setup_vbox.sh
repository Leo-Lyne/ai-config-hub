#!/usr/bin/env bash
# One-shot provisioning for the vbox-linux transport.
#
# Idempotent — re-running is safe. Each step checks state and skips if done.
#
# What it does:
#   1. Verify (or hint to install) VirtualBox via winget on Windows host.
#   2. Verify (or auto-install) the matching VBox Extension Pack.
#   3. Download Ubuntu cloud image (jammy) if not cached.
#   4. Generate per-user cloud-init seed.iso (SSH key, hostname, etc.).
#   5. Build VM disk (vdi) from cloud image + seed.iso.
#   6. Create VBox VM if missing; configure NAT+SSH PF + USB filters.
#   7. Stage v2.4 upgrade_tool into deps/ if not present.
#   8. First boot to let cloud-init run, then verify SSH works.
#
# Override anything via the env vars in lib/config.sh — every config knob is
# parametric, no hardcoded paths.

set -eu

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"

# Bundled artifacts (offline-first). Falls back to download only if missing.
PKG_DIR="$RK_SKILL_ROOT/deps/packages"
BUNDLED_VBOX_INSTALLER="$(ls -1 "$PKG_DIR"/VirtualBox-*-Win.exe 2>/dev/null | head -1)"
BUNDLED_EXTPACK="$(ls -1 "$PKG_DIR"/Oracle_VirtualBox_Extension_Pack-*.vbox-extpack 2>/dev/null | head -1)"
BUNDLED_CLOUD_IMG="$(ls -1 "$PKG_DIR"/jammy-server-cloudimg-amd64.img 2>/dev/null | head -1)"

# Online URLs (only used if bundled artifact is absent)
CLOUD_IMG_URL="${RK_CLOUD_IMG_URL:-https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img}"

# Working paths
CLOUD_IMG_LX="${RK_CLOUD_IMG_LX:-$RK_VBOX_DIR_LX/jammy-cloud.img}"
SEED_ISO_LX="${RK_SEED_ISO_LX:-$RK_VBOX_DIR_LX/${RK_VBOX_VM_NAME}-seed.iso}"
SEED_ISO_W="${RK_SEED_ISO_W:-$RK_VBOX_DIR_W\\${RK_VBOX_VM_NAME}-seed.iso}"

# ─────────────────────────────────────────────────────────────────────────────
# 1. VirtualBox installed?
# ─────────────────────────────────────────────────────────────────────────────
ensure_vbox() {
    if [ -x "$RK_VBOX_BIN" ]; then
        log "VirtualBox: $("$RK_VBOX_BIN" --version)"
        return 0
    fi
    # Bundled installer first (offline-friendly)
    if [ -n "$BUNDLED_VBOX_INSTALLER" ] && [ -f "$BUNDLED_VBOX_INSTALLER" ]; then
        log "Installing VirtualBox from bundled $BUNDLED_VBOX_INSTALLER (silent)..."
        local win_path; win_path="$(wslpath -w "$BUNDLED_VBOX_INSTALLER" 2>/dev/null || true)"
        if [ -n "$win_path" ]; then
            cmd.exe /c start /wait "$win_path" --silent --ignore-reboot || true
            sleep 2
            if [ -x "$RK_VBOX_BIN" ]; then
                log "Installed (from bundle): $("$RK_VBOX_BIN" --version)"
                return 0
            fi
        fi
    fi
    # Online fallback
    if command -v winget.exe >/dev/null 2>&1 || [ -x "/mnt/c/Windows/System32/winget.exe" ]; then
        log "Installing VirtualBox via winget (online)..."
        local winget="/mnt/c/Windows/System32/winget.exe"
        [ -x "$winget" ] || winget="winget.exe"
        "$winget" install --id Oracle.VirtualBox -e \
            --accept-package-agreements --accept-source-agreements --silent || true
        if [ -x "$RK_VBOX_BIN" ]; then
            log "Installed: $("$RK_VBOX_BIN" --version)"
            return 0
        fi
    fi
    die "VirtualBox not found at $RK_VBOX_BIN.
  Provision via:    bash $RK_SKILL_ROOT/deps/fetch_deps.sh   (online machine, then re-run setup)
  Or install:       winget install Oracle.VirtualBox
  Or override:      export RK_VBOX_BIN=/path/to/VBoxManage[.exe]"
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Extension Pack installed (matching VBox version)?
# ─────────────────────────────────────────────────────────────────────────────
ensure_extpack() {
    if "$RK_VBOX_BIN" list extpacks 2>/dev/null | grep -q '^Pack no\.'; then
        log "Extension Pack: $("$RK_VBOX_BIN" list extpacks 2>/dev/null | awk '/^Pack no\./{f=1;next} f && /^Version:/{print $2; exit}')"
        return 0
    fi
    local ver; ver="$("$RK_VBOX_BIN" --version | sed 's/r.*//')"  # e.g. 7.2.8
    local fname="Oracle_VirtualBox_Extension_Pack-${ver}.vbox-extpack"
    local src=""
    # Bundled first (must match installed VBox version)
    if [ -n "$BUNDLED_EXTPACK" ] && [ -f "$BUNDLED_EXTPACK" ]; then
        if [[ "$(basename "$BUNDLED_EXTPACK")" == *"$ver"* ]]; then
            src="$BUNDLED_EXTPACK"
            log "Using bundled extpack: $src"
        else
            log "bundled extpack version mismatch (need $ver, have $(basename "$BUNDLED_EXTPACK")); will download"
        fi
    fi
    if [ -z "$src" ]; then
        local url="https://download.virtualbox.org/virtualbox/${ver}/${fname}"
        src="${TMPDIR:-/tmp}/$fname"
        log "Downloading Extension Pack ${ver}..."
        curl -fL -o "$src" "$url" || die "extpack download failed: $url"
    fi
    # Copy to Windows-accessible path (VBoxManage.exe can't read WSL ext4 paths)
    local win_dl="/mnt/c/Users/Public/$fname"
    cp "$src" "$win_dl"
    log "Installing Extension Pack..."
    printf 'y\n' | "$RK_VBOX_BIN" extpack install "$(wslpath -w "$win_dl" 2>/dev/null || echo "C:\\Users\\Public\\$fname")"
    rm -f "$win_dl"
    [ "$src" = "$BUNDLED_EXTPACK" ] || rm -f "$src"
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. Cloud image cached?
# ─────────────────────────────────────────────────────────────────────────────
ensure_cloud_image() {
    [ -f "$CLOUD_IMG_LX" ] && [ "$(stat -c%s "$CLOUD_IMG_LX")" -gt 100000000 ] && {
        log "Cloud image: $CLOUD_IMG_LX ($(du -h "$CLOUD_IMG_LX" | cut -f1))"
        return 0
    }
    mkdir -p "$RK_VBOX_DIR_LX"
    # Bundled first
    if [ -n "$BUNDLED_CLOUD_IMG" ] && [ -f "$BUNDLED_CLOUD_IMG" ]; then
        log "Copying bundled cloud image → $CLOUD_IMG_LX..."
        cp "$BUNDLED_CLOUD_IMG" "$CLOUD_IMG_LX"
        return 0
    fi
    log "Downloading Ubuntu cloud image (bundled copy not found, falling back to online)..."
    log "  URL: $CLOUD_IMG_URL"
    log "  Dest: $CLOUD_IMG_LX (~700 MB, takes a few min)"
    log "  Tip: bash $RK_SKILL_ROOT/deps/fetch_deps.sh   to bundle for offline reuse"
    curl -fL -o "$CLOUD_IMG_LX.partial" "$CLOUD_IMG_URL"
    mv "$CLOUD_IMG_LX.partial" "$CLOUD_IMG_LX"
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. cloud-init seed.iso (SSH key, hostname, etc.) for this user
# ─────────────────────────────────────────────────────────────────────────────
ensure_seed_iso() {
    [ -f "$SEED_ISO_LX" ] && {
        log "seed.iso: $SEED_ISO_LX"
        return 0
    }
    [ -f "$RK_VBOX_SSH_KEY.pub" ] || die "missing SSH pubkey: $RK_VBOX_SSH_KEY.pub
  Generate one:  ssh-keygen -t ed25519 -f $RK_VBOX_SSH_KEY -N ''"

    have genisoimage || have mkisofs || die "need genisoimage or mkisofs to build seed.iso (apt install genisoimage)"
    local tmpd; tmpd="$(mktemp -d)"
    trap 'rm -rf "$tmpd"' RETURN

    local pubkey; pubkey="$(<"$RK_VBOX_SSH_KEY.pub")"
    local instance_id="rk3568-flash-$(date +%s)"
    sed -e "s|\${HOSTNAME}|$RK_VBOX_VM_NAME|g" \
        -e "s|\${SSH_USER}|$RK_VBOX_SSH_USER|g" \
        -e "s|\${SSH_PUBKEY}|$pubkey|g" \
        "$RK_SKILL_ROOT/deps/cloud_init/user-data.tmpl" > "$tmpd/user-data"
    sed -e "s|\${INSTANCE_ID}|$instance_id|g" \
        -e "s|\${HOSTNAME}|$RK_VBOX_VM_NAME|g" \
        "$RK_SKILL_ROOT/deps/cloud_init/meta-data.tmpl" > "$tmpd/meta-data"
    cp "$RK_SKILL_ROOT/deps/cloud_init/network-config.tmpl" "$tmpd/network-config"

    log "Building seed.iso..."
    if have genisoimage; then
        genisoimage -output "$SEED_ISO_LX" -volid CIDATA -joliet -rock \
            "$tmpd/user-data" "$tmpd/meta-data" "$tmpd/network-config" 2>/dev/null
    else
        mkisofs -output "$SEED_ISO_LX" -volid CIDATA -joliet -rock \
            "$tmpd/user-data" "$tmpd/meta-data" "$tmpd/network-config" 2>/dev/null
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# 5. Build vdi disk from cloud image
# ─────────────────────────────────────────────────────────────────────────────
ensure_vdi() {
    [ -f "$RK_VBOX_VDI_LX" ] && {
        log "vdi: $RK_VBOX_VDI_LX ($(du -h "$RK_VBOX_VDI_LX" | cut -f1))"
        return 0
    }
    log "Converting cloud-image qcow2 → vdi at $RK_VBOX_VDI_LX..."
    have qemu-img || die "need qemu-img to convert cloud image (apt install qemu-utils)"
    qemu-img convert -O vdi "$CLOUD_IMG_LX" "$RK_VBOX_VDI_LX"
    # Resize to 40 GB so growpart has room
    "$RK_VBOX_BIN" modifyhd "$(wslpath -w "$RK_VBOX_VDI_LX" 2>/dev/null || echo "$RK_VBOX_VDI_W")" \
        --resize 40960 || true
}

# ─────────────────────────────────────────────────────────────────────────────
# 6. VBox VM exists + configured
# ─────────────────────────────────────────────────────────────────────────────
ensure_vm() {
    if "$RK_VBOX_BIN" list vms 2>/dev/null | grep -q "\"$RK_VBOX_VM_NAME\""; then
        log "VM '$RK_VBOX_VM_NAME' already exists."
    else
        log "Creating VM '$RK_VBOX_VM_NAME'..."
        "$RK_VBOX_BIN" setproperty machinefolder "$RK_VBOX_DIR_W" >/dev/null
        "$RK_VBOX_BIN" createvm --name "$RK_VBOX_VM_NAME" --ostype Ubuntu_64 --register
        "$RK_VBOX_BIN" modifyvm "$RK_VBOX_VM_NAME" \
            --memory "$RK_VBOX_VM_MEMORY" --cpus "$RK_VBOX_VM_CPUS" --audio-driver none \
            --nic1 nat --usbxhci on --acpi on --boot1 disk --rtcuseutc on \
            --macaddress1 "$RK_VBOX_NIC_MAC" --nictype1 82540EM
        "$RK_VBOX_BIN" storagectl "$RK_VBOX_VM_NAME" --name SATA --add sata --controller IntelAhci --portcount 2
        "$RK_VBOX_BIN" storageattach "$RK_VBOX_VM_NAME" --storagectl SATA --port 0 --device 0 \
            --type hdd --medium "$RK_VBOX_VDI_W"
        "$RK_VBOX_BIN" storagectl "$RK_VBOX_VM_NAME" --name IDE --add ide --controller PIIX4
        "$RK_VBOX_BIN" storageattach "$RK_VBOX_VM_NAME" --storagectl IDE --port 0 --device 0 \
            --type dvddrive --medium "$SEED_ISO_W"
        "$RK_VBOX_BIN" modifyvm "$RK_VBOX_VM_NAME" --natpf1 "ssh,tcp,$RK_VBOX_SSH_HOST,$RK_VBOX_SSH_PORT,,22"
    fi

    # USB filters (idempotent)
    local n; n="$("$RK_VBOX_BIN" showvminfo "$RK_VBOX_VM_NAME" --machinereadable 2>/dev/null | grep -c '^USBFilterName')"
    if [ "$n" -lt 3 ]; then
        log "Adding USB filters..."
        "$RK_VBOX_BIN" usbfilter add 0 --target "$RK_VBOX_VM_NAME" --name rockusb-loader  --vendorid 2207 --productid 350a --active yes 2>/dev/null || true
        "$RK_VBOX_BIN" usbfilter add 1 --target "$RK_VBOX_VM_NAME" --name rockusb-maskrom --vendorid 2207 --productid 350b --active yes 2>/dev/null || true
        "$RK_VBOX_BIN" usbfilter add 2 --target "$RK_VBOX_VM_NAME" --name rockchip-adb    --vendorid 2207 --productid 0006 --active yes 2>/dev/null || true
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# 7. Stage Linux upgrade_tool v2.4 into deps/ (so vbox transport is self-contained)
# ─────────────────────────────────────────────────────────────────────────────
ensure_linux_upgrade_tool_in_deps() {
    local dst="$RK_SKILL_ROOT/deps/linux_upgrade_tool/upgrade_tool"
    [ -x "$dst" ] && { log "v2.4 upgrade_tool already in deps/"; return 0; }
    require_utool_linux  # may auto-extract from BSP zip
    log "copying v2.4 upgrade_tool into deps/ (3 MB, one-time)..."
    mkdir -p "$(dirname "$dst")"
    cp "$RK_UTOOL_LINUX" "$dst"
    chmod +x "$dst"
}

# ─────────────────────────────────────────────────────────────────────────────
# 8. First boot + SSH verify
# ─────────────────────────────────────────────────────────────────────────────
first_boot_verify() {
    if "$RK_VBOX_BIN" list runningvms 2>/dev/null | grep -q "\"$RK_VBOX_VM_NAME\""; then
        log "VM already running; skipping boot."
    else
        log "First boot — cloud-init runs once, takes ~3-5 min..."
        "$RK_VBOX_BIN" startvm "$RK_VBOX_VM_NAME" --type headless | tail -1
    fi

    local ssh_target="${RK_VBOX_SSH_USER}@${RK_VBOX_SSH_HOST}"
    local ssh_opts=(-p "$RK_VBOX_SSH_PORT" -i "$RK_VBOX_SSH_KEY"
                    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
                    -o ConnectTimeout=4 -o BatchMode=yes)
    log "waiting up to 5 min for SSH..."
    local t=0
    while ! ssh "${ssh_opts[@]}" "$ssh_target" 'echo OK' 2>/dev/null; do
        sleep 8; t=$((t+8))
        [ "$t" -ge 300 ] && die "SSH not ready after 5 min"
    done
    log "SSH OK. Guest: $(ssh "${ssh_opts[@]}" "$ssh_target" 'uname -srm; cat /etc/os-release | grep PRETTY')"
}

# ─────────────────────────────────────────────────────────────────────────────
main() {
    log "=== rk3568-flash vbox-linux setup ==="
    print_config_summary
    log ""
    ensure_vbox
    ensure_extpack
    ensure_cloud_image
    ensure_seed_iso
    ensure_vdi
    ensure_vm
    ensure_linux_upgrade_tool_in_deps
    first_boot_verify
    log ""
    log "=== setup complete ==="
    log "Try: bash $RK_SKILL_ROOT/scripts/flash.sh vbox-linux status"
}

main "$@"
