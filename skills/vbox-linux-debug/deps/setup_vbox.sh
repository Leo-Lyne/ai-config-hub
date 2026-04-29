#!/usr/bin/env bash
# One-shot VM provisioning for /vbox-linux-debug.
#
# Idempotent — re-running is safe. Each step checks state first.
#
# Steps:
#   1. Verify (or auto-install) VirtualBox using bundled installer / winget.
#   2. Verify (or auto-install) the matching Extension Pack.
#   3. Cache Ubuntu cloud image (bundled or download).
#   4. Generate per-user cloud-init seed.iso (SSH key, hostname).
#   5. Build VM disk (vdi) from cloud image.
#   6. Create VBox VM if missing; configure NAT+SSH PF.
#   7. First boot to let cloud-init run, then verify SSH works.
#
# Override anything via env vars in lib/config.sh.

set -eu

. "$(dirname "${BASH_SOURCE[0]}")/../lib/config.sh"

PKG_DIR="$VBLD_SKILL_ROOT/deps/packages"
BUNDLED_VBOX_INSTALLER="$(ls -1 "$PKG_DIR"/VirtualBox-*-Win.exe 2>/dev/null | head -1)"
BUNDLED_EXTPACK="$(ls -1 "$PKG_DIR"/Oracle_VirtualBox_Extension_Pack-*.vbox-extpack 2>/dev/null | head -1)"
BUNDLED_CLOUD_IMG="$(ls -1 "$PKG_DIR"/jammy-server-cloudimg-amd64.img 2>/dev/null | head -1)"

CLOUD_IMG_URL="${VBLD_CLOUD_IMG_URL:-https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img}"
CLOUD_IMG_LX="${VBLD_CLOUD_IMG_LX:-$VBLD_VM_DIR_LX/jammy-cloud.img}"
SEED_ISO_LX="${VBLD_SEED_ISO_LX:-$VBLD_VM_DIR_LX/${VBLD_VM_NAME}-seed.iso}"
SEED_ISO_W="${VBLD_SEED_ISO_W:-$(_lx_to_winpath "$SEED_ISO_LX")}"

# ─────────────────────────────────────────────────────────────────────────────
ensure_vbox() {
    if vbm_present; then
        log "VirtualBox: $("$VBLD_VBOX_BIN" --version)"
        return 0
    fi
    if [ -n "$BUNDLED_VBOX_INSTALLER" ] && _is_wsl; then
        log "Installing VirtualBox from bundled $BUNDLED_VBOX_INSTALLER (silent)..."
        local win_path; win_path="$(_lx_to_winpath "$BUNDLED_VBOX_INSTALLER")"
        cmd.exe /c start /wait "$win_path" --silent --ignore-reboot 2>/dev/null || true
        sleep 2
        VBLD_VBOX_BIN="$(_detect_vbox_bin)"
        vbm_present && { log "Installed (from bundle): $("$VBLD_VBOX_BIN" --version)"; return 0; }
    fi
    if _is_wsl && [ -x "/mnt/c/Windows/System32/winget.exe" ]; then
        log "Installing VirtualBox via winget (online)..."
        /mnt/c/Windows/System32/winget.exe install --id Oracle.VirtualBox -e \
            --accept-package-agreements --accept-source-agreements --silent || true
        VBLD_VBOX_BIN="$(_detect_vbox_bin)"
        vbm_present && { log "Installed: $("$VBLD_VBOX_BIN" --version)"; return 0; }
    fi
    die "VirtualBox not found.
  Provision via:    bash $VBLD_SKILL_ROOT/deps/fetch_deps.sh   (online machine, then re-run setup)
  Or install:       winget install Oracle.VirtualBox  (Windows host, WSL2)
  Or override:      export VBLD_VBOX_BIN=/path/to/VBoxManage[.exe]"
}

ensure_extpack() {
    if extpack_installed; then
        log "Extension Pack already installed."
        return 0
    fi
    local ver; ver="$("$VBLD_VBOX_BIN" --version | sed 's/r.*//')"
    local fname="Oracle_VirtualBox_Extension_Pack-${ver}.vbox-extpack"
    local src=""
    if [ -n "$BUNDLED_EXTPACK" ] && [[ "$(basename "$BUNDLED_EXTPACK")" == *"$ver"* ]]; then
        src="$BUNDLED_EXTPACK"
        log "Using bundled extpack: $src"
    fi
    if [ -z "$src" ]; then
        local url="https://download.virtualbox.org/virtualbox/${ver}/${fname}"
        src="${TMPDIR:-/tmp}/$fname"
        log "Downloading Extension Pack ${ver}..."
        curl -fL -o "$src" "$url" || die "extpack download failed: $url"
    fi
    if _is_wsl; then
        local win_dl="/mnt/c/Users/Public/$fname"
        cp "$src" "$win_dl"
        printf 'y\n' | "$VBLD_VBOX_BIN" extpack install "$(_lx_to_winpath "$win_dl")"
        rm -f "$win_dl"
    else
        printf 'y\n' | "$VBLD_VBOX_BIN" extpack install "$src"
    fi
    [ "$src" = "$BUNDLED_EXTPACK" ] || rm -f "$src"
}

ensure_cloud_image() {
    [ -f "$CLOUD_IMG_LX" ] && [ "$(stat -c%s "$CLOUD_IMG_LX")" -gt 100000000 ] && {
        log "Cloud image: $CLOUD_IMG_LX ($(du -h "$CLOUD_IMG_LX" | cut -f1))"
        return 0
    }
    mkdir -p "$VBLD_VM_DIR_LX"
    if [ -n "$BUNDLED_CLOUD_IMG" ]; then
        log "Copying bundled cloud image → $CLOUD_IMG_LX..."
        cp "$BUNDLED_CLOUD_IMG" "$CLOUD_IMG_LX"
        return 0
    fi
    log "Downloading Ubuntu cloud image (~700 MB) → $CLOUD_IMG_LX..."
    log "  Tip: bash $VBLD_SKILL_ROOT/deps/fetch_deps.sh   to bundle for offline reuse"
    curl -fL -o "$CLOUD_IMG_LX.partial" "$CLOUD_IMG_URL"
    mv "$CLOUD_IMG_LX.partial" "$CLOUD_IMG_LX"
}

ensure_seed_iso() {
    [ -f "$SEED_ISO_LX" ] && { log "seed.iso: $SEED_ISO_LX"; return 0; }
    [ -f "$VBLD_SSH_KEY.pub" ] || die "missing SSH pubkey: $VBLD_SSH_KEY.pub
  Generate:  ssh-keygen -t ed25519 -f $VBLD_SSH_KEY -N ''"
    have genisoimage || have mkisofs || die "need genisoimage or mkisofs (apt install genisoimage)"

    local tmpd; tmpd="$(mktemp -d)"
    trap 'rm -rf "$tmpd"' RETURN

    local pubkey; pubkey="$(<"$VBLD_SSH_KEY.pub")"
    local instance_id="vbld-$(date +%s)"
    sed -e "s|\${HOSTNAME}|$VBLD_VM_NAME|g" \
        -e "s|\${SSH_USER}|$VBLD_SSH_USER|g" \
        -e "s|\${SSH_PUBKEY}|$pubkey|g" \
        "$VBLD_SKILL_ROOT/deps/cloud_init/user-data.tmpl" > "$tmpd/user-data"
    sed -e "s|\${INSTANCE_ID}|$instance_id|g" \
        -e "s|\${HOSTNAME}|$VBLD_VM_NAME|g" \
        "$VBLD_SKILL_ROOT/deps/cloud_init/meta-data.tmpl" > "$tmpd/meta-data"
    cp "$VBLD_SKILL_ROOT/deps/cloud_init/network-config.tmpl" "$tmpd/network-config"

    log "Building seed.iso → $SEED_ISO_LX..."
    if have genisoimage; then
        genisoimage -output "$SEED_ISO_LX" -volid CIDATA -joliet -rock \
            "$tmpd/user-data" "$tmpd/meta-data" "$tmpd/network-config" 2>/dev/null
    else
        mkisofs -output "$SEED_ISO_LX" -volid CIDATA -joliet -rock \
            "$tmpd/user-data" "$tmpd/meta-data" "$tmpd/network-config" 2>/dev/null
    fi
}

ensure_vdi() {
    [ -f "$VBLD_VDI_LX" ] && { log "vdi: $VBLD_VDI_LX ($(du -h "$VBLD_VDI_LX" | cut -f1))"; return 0; }
    have qemu-img || die "need qemu-img to convert cloud image (apt install qemu-utils)"
    log "Converting cloud-image → vdi at $VBLD_VDI_LX..."
    qemu-img convert -O vdi "$CLOUD_IMG_LX" "$VBLD_VDI_LX"
    "$VBLD_VBOX_BIN" modifyhd "$VBLD_VDI_W" --resize 40960 || true
}

# Generate a stable random VBox-OUI (08:00:27) MAC if user hasn't set one.
ensure_mac() {
    if [ -n "$VBLD_VM_NIC_MAC" ]; then
        return 0
    fi
    local mac; mac="080027$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n' | tr 'a-z' 'A-Z')"
    VBLD_VM_NIC_MAC="$mac"
    log "Generated VM MAC: $VBLD_VM_NIC_MAC"
    log "  → persisting via $VBLD_OVERRIDE_FILE so subsequent runs match netplan seal."
    mkdir -p "$(dirname "$VBLD_OVERRIDE_FILE")"
    {
        echo "# vbox-linux-debug per-host overrides — auto-generated $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "export VBLD_VM_NIC_MAC=$VBLD_VM_NIC_MAC"
    } >> "$VBLD_OVERRIDE_FILE"
}

ensure_vm() {
    if vm_exists; then
        log "VM '$VBLD_VM_NAME' already exists."
    else
        ensure_mac
        log "Creating VM '$VBLD_VM_NAME'..."
        "$VBLD_VBOX_BIN" setproperty machinefolder "$VBLD_VM_DIR_W" >/dev/null
        "$VBLD_VBOX_BIN" createvm --name "$VBLD_VM_NAME" --ostype Ubuntu_64 --register
        "$VBLD_VBOX_BIN" modifyvm "$VBLD_VM_NAME" \
            --memory "$VBLD_VM_MEMORY" --cpus "$VBLD_VM_CPUS" --audio-driver none \
            --nic1 nat --usbxhci on --acpi on --boot1 disk --rtcuseutc on \
            --macaddress1 "$VBLD_VM_NIC_MAC" --nictype1 "$VBLD_VM_NIC_TYPE"
        "$VBLD_VBOX_BIN" storagectl "$VBLD_VM_NAME" --name SATA --add sata --controller IntelAhci --portcount 2
        "$VBLD_VBOX_BIN" storageattach "$VBLD_VM_NAME" --storagectl SATA --port 0 --device 0 \
            --type hdd --medium "$VBLD_VDI_W"
        "$VBLD_VBOX_BIN" storagectl "$VBLD_VM_NAME" --name IDE --add ide --controller PIIX4
        "$VBLD_VBOX_BIN" storageattach "$VBLD_VM_NAME" --storagectl IDE --port 0 --device 0 \
            --type dvddrive --medium "$SEED_ISO_W"
        "$VBLD_VBOX_BIN" modifyvm "$VBLD_VM_NAME" --natpf1 "ssh,tcp,$VBLD_SSH_HOST,$VBLD_SSH_PORT,,22"
        log "VM '$VBLD_VM_NAME' created."
    fi
}

first_boot_verify() {
    if vm_running; then
        log "VM already running; skipping boot."
    else
        log "First boot — cloud-init runs once, takes ~3-5 min..."
        "$VBLD_VBOX_BIN" startvm "$VBLD_VM_NAME" --type headless | tail -1
    fi
    log "waiting up to 5 min for SSH..."
    wait_ssh 300
    log "SSH OK. Guest: $(ssh_vm 'uname -srm; cat /etc/os-release | grep PRETTY')"
}

main() {
    log "=== vbox-linux-debug / setup ==="
    print_config_summary
    log ""
    ensure_vbox
    ensure_extpack
    ensure_cloud_image
    ensure_seed_iso
    ensure_vdi
    ensure_vm
    first_boot_verify
    log ""
    log "=== setup complete ==="
    log "Try: bash $VBLD_SKILL_ROOT/scripts/status.sh"
}

main "$@"
