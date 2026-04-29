# rk3568-flash / deps

Self-contained dependencies for both transports of the `/rk3568-flash` skill.

## What's bundled (committed to repo)

| Path | Size | Why |
|---|---|---|
| `linux_upgrade_tool/upgrade_tool` | ~3 MB | Rockchip's Linux v2.4 flasher binary. Used by the `vbox-linux` transport inside the VM. Bundled because the v1.54 in `rkbin/tools/` is a different (broken) build, and not every BSP repo ships v2.4 unzipped. |
| `cloud_init/user-data.tmpl` | <1 KB | Renders to `user-data` in seed.iso. Sets up SSH key auth + installs adb + udev rule for VID 2207. |
| `cloud_init/meta-data.tmpl` | <1 KB | Renders to `meta-data` in seed.iso. |
| `cloud_init/network-config.tmpl` | <1 KB | Renders to `network-config` in seed.iso. Matches `en*` so any NIC name works (no MAC pinning). |
| `setup_vbox.sh` | ~6 KB | Idempotent provisioning: VBox → extpack → cloud image → seed.iso → vdi → VM → first boot. |

## What's downloaded on demand (not committed)

| Resource | Source | Size | When fetched |
|---|---|---|---|
| VirtualBox 7.x | `winget install Oracle.VirtualBox` | ~120 MB | First `setup_vbox.sh` run if not already installed |
| VirtualBox Extension Pack | `https://download.virtualbox.org/virtualbox/<ver>/Oracle_VirtualBox_Extension_Pack-<ver>.vbox-extpack` | ~20 MB | First `setup_vbox.sh` run; version auto-matched to installed VBox |
| Ubuntu jammy cloud image | `https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img` | ~700 MB | First `setup_vbox.sh` run; cached at `${RK_VBOX_DIR_LX}/jammy-cloud.img` |

Override URLs via `RK_CLOUD_IMG_URL`, paths via `RK_CLOUD_IMG_LX` etc.

## Setup

```
bash ~/.claude/skills/rk3568-flash/deps/setup_vbox.sh
```

Idempotent: each step checks state and skips if already done.

## Why this transport at all

Sometimes you can't (or won't) use Windows-side `upgrade_tool.exe`:

- WSL2 has no Windows host (e.g. fresh laptop / CI)
- You suspect Windows USB driver is misbehaving
- You want a reproducible Linux flashing env

The vbox-linux transport gives you an isolated Linux VM that owns the Rockusb
device via VBox USB filters (auto-snatch by VID/PID — works headless from CLI,
unlike VMware Workstation which has no working USB CLI).

## Removing

```
# Inside-Windows side
"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" unregistervm rk-burn --delete
rm -rf /mnt/d/VBoxVMs/rk-burn-seed.iso /mnt/d/VBoxVMs/rk-burn.vdi
# Optionally:
"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe" extpack uninstall "Oracle VirtualBox Extension Pack"
winget uninstall Oracle.VirtualBox
```

This skill never touches your other VMs or VBox state.
