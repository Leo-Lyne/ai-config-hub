# vbox-linux-debug / deps

Self-contained dependencies for the `/vbox-linux-debug` skill — a headless
VirtualBox-based Linux VM with reliable USB device pass-through.

## What's bundled (committed to repo)

| Path | Size | Why |
|---|---|---|
| `cloud_init/user-data.tmpl` | <1 KB | cloud-init `user-data` template (SSH key, hostname, generic `0666` USB udev rule). |
| `cloud_init/meta-data.tmpl` | <1 KB | cloud-init `meta-data` template. |
| `cloud_init/network-config.tmpl` | <1 KB | cloud-init `network-config` — matches `en*` so NIC name doesn't matter. |
| `setup_vbox.sh` | ~6 KB | Idempotent provisioning: VBox → extpack → cloud image → seed.iso → vdi → VM → first boot. |
| `fetch_deps.sh` | ~2 KB | Populates `packages/` with VBox installer + extpack + cloud image (~840 MB). |

## What's downloaded by `fetch_deps.sh` (into `packages/`, gitignored)

| Resource | Source | Size |
|---|---|---|
| VirtualBox 7.x (Win installer) | `https://download.virtualbox.org/virtualbox/<ver>/VirtualBox-<ver>-*-Win.exe` | ~120 MB |
| VirtualBox Extension Pack | `https://download.virtualbox.org/virtualbox/<ver>/Oracle_VirtualBox_Extension_Pack-<ver>.vbox-extpack` | ~20 MB |
| Ubuntu jammy cloud image | `https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img` | ~700 MB |

Override URLs/paths via env vars (`VBLD_VBOX_INSTALLER_URL`, `VBLD_CLOUD_IMG_URL`, etc.). See `lib/config.sh`.

## Workflow

**First-time setup (online machine):**

```bash
bash ~/.claude/skills/vbox-linux-debug/deps/fetch_deps.sh   # downloads ~840 MB into packages/
bash ~/.claude/skills/vbox-linux-debug/scripts/setup.sh     # builds VM
```

**Offline / reproducible setup:** commit `deps/packages/*` to your skill repo
(or copy them across machines manually). Then `setup.sh` finds the bundled
artifacts and skips the network entirely.

**Per-host overrides:** `setup_vbox.sh` writes the auto-generated VM MAC to
`~/.config/vbox-linux-debug/env` on first run. Add other overrides there too:

```sh
# ~/.config/vbox-linux-debug/env
export VBLD_VM_DIR_LX=/mnt/e/MyVMs
export VBLD_VBOX_VM_MEMORY=2048
```

## Removing

```bash
"$VBLD_VBOX_BIN" unregistervm "$VBLD_VM_NAME" --delete
rm -rf "$VBLD_VM_DIR_LX/${VBLD_VM_NAME}-seed.iso" "$VBLD_VDI_LX"
```

This skill never touches your other VMs or VBox state.
