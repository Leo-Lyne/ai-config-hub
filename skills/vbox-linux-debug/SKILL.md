---
name: vbox-linux-debug
description: Headless VirtualBox-based Linux VM with reliable USB device pass-through, used to debug/flash embedded targets from WSL2 (where usbipd cannot reliably forward certain low-speed/control-protocol devices). Subcommands — `setup` / `up` / `down` / `status` / `shell` / `usb {list,add,rm}`. The VBox layer auto-snatches USB devices into the VM by VID/PID filter (something VMware Workstation cannot do via CLI). Use when the user says /vbox-linux-debug, asks to debug/flash a target via a Linux VM, says "VBox 调试" / "用虚拟机连板子" / "Linux 虚拟机透传 USB", or hits a wall where WSL2 usbipd can't pass through a specific device. Generic VM + USB primitive — domain skills (rk3568-flash, openocd, jtag, custom-tool) compose it.
---

# vbox-linux-debug — Linux VM with reliable USB pass-through, headless from CLI

## Why this exists

WSL2's `usbipd-win` cannot reliably pass through certain USB devices — most
notably Rockchip Rockusb (VID 2207, low-speed control transfer protocol) and
some other vendor-protocol devices. Linux-native debug/flash tools
(`rkdeveloptool`, `upgrade_tool`, `openocd`, custom Linux tooling) don't have a
clean Windows path inside WSL2 either.

This skill's solution: run a headless **VirtualBox** Ubuntu VM that owns the
device via VBox's USB filter (VID/PID match → auto-snatch from host). All
control is CLI — `VBoxManage` + `ssh` over a NAT port-forward. No GUI required.

VMware Workstation's `vmrun` lacks any equivalent USB CLI — it can't do this
headlessly.

## Configuration (zero hardcoded paths)

All paths auto-detect; override via env vars or via
`~/.config/vbox-linux-debug/env`. See `lib/config.sh` for the full list. Most
common:

| Env var | Default | What |
|---|---|---|
| `VBLD_VBOX_BIN` | WSL2: `/mnt/c/Program Files/Oracle/VirtualBox/VBoxManage.exe`; native: `$(command -v VBoxManage)` | VBoxManage binary |
| `VBLD_VM_NAME` | `rk-burn` (back-compat) | VBox VM name |
| `VBLD_VM_DIR_LX` | first existing of `/mnt/d/VBoxVMs`, `/mnt/c/VBoxVMs`, `/mnt/c/Users/Public/VBoxVMs`; native: `$HOME/VBoxVMs` | VM disk folder (must be Win-accessible on WSL2) |
| `VBLD_VDI_LX` | `<VM_DIR>/<VM_NAME>.vdi` | VM disk |
| `VBLD_VM_NIC_MAC` | empty → randomized + persisted on first `setup` | MAC pinned for cloud-init netplan seal |
| `VBLD_SSH_USER` | `$USER` | SSH user inside the VM (must match cloud-init seed) |
| `VBLD_SSH_PORT` | `2222` | NAT port-forward target |
| `VBLD_SSH_KEY` | `$HOME/.ssh/id_ed25519` | SSH key for VM access |

Per-host overrides also persist at `${XDG_CONFIG_HOME:-$HOME/.config}/vbox-linux-debug/env`.

To see what got resolved: `bash scripts/status.sh` prints the config summary.

## First-time setup

```bash
SKILL=$HOME/.claude/skills/vbox-linux-debug

# (online machine) Bundle VBox + extpack + cloud image into deps/packages/
bash $SKILL/deps/fetch_deps.sh

# Then provision the VM (idempotent — re-runnable)
bash $SKILL/scripts/setup.sh
```

`setup.sh` walks: install VBox → install extpack → cache cloud image → render
cloud-init seed.iso (with your SSH pubkey) → build vdi → create VM → first boot
→ verify SSH. Each step is idempotent; re-running fixes whatever's missing.

If `deps/packages/` is empty, `setup.sh` falls back to online downloads. Bundle
once + commit `deps/packages/*` to your repo for reproducible offline setup.

## Sub-commands

```
SKILL=$HOME/.claude/skills/vbox-linux-debug
```

### `setup` — bootstrap (idempotent)
```
bash $SKILL/scripts/setup.sh
```

### `up [--wait[=SEC]]` — start VM headless
```
bash $SKILL/scripts/up.sh
```
Default: starts headless and blocks up to 300s for SSH. Use `--no-wait` to fire-and-forget.

First boot after `setup` takes ~3-5 min (cloud-init + emulated AHCI). Subsequent boots are faster.

### `down [--hard]`
ACPI shutdown by default (waits up to ~40 s). `--hard` for force-poweroff.

### `status` — diagnostics
Prints config summary, VM state, USB filters, host USB candidates, what's currently attached to the VM, SSH reachability with a quick guest probe.

### `shell [cmd...]`
- No args → drop into interactive `ssh`.
- With args → `ssh ... <cmd>` (one-shot, stdout returned).

### `usb list | add VID:PID [name] | rm <name>`
Manage USB filters. Filter format: hex without `0x`, e.g. `usb add 1a86:7523 ch340-uart`. Filters auto-snatch matching devices when the VM is running.

The VM ships with **no domain-specific filters**. Add the ones you need:

```
# Rockchip Rockusb (RK3568 burn mode — Loader + Maskrom)
bash $SKILL/scripts/usb.sh add 2207:350a rockusb-loader
bash $SKILL/scripts/usb.sh add 2207:350b rockusb-maskrom

# CH340 USB-UART
bash $SKILL/scripts/usb.sh add 1a86:7523 ch340-uart

# CP210x USB-UART
bash $SKILL/scripts/usb.sh add 10c4:ea60 cp210x-uart
```

## Composing with domain skills

This skill is the **primitive layer** (VM + USB filter + SSH). Domain workflows compose it:

| Domain task | Skill | How it uses vbox-linux-debug |
|---|---|---|
| RK3568 flashing on Linux | **`/rk3568-flash vbox-linux …`** | Uses its own VM provisioning + `upgrade_tool` invocation; this skill is **not** its dependency. |
| Custom Linux tooling against a USB device | this skill directly | `usb add VID:PID`, `up`, `shell '<linux-tool> <args>'` |
| OpenOCD / JTAG via Linux | this skill (drop a script in `scripts/`) | Same primitives — see "Extending" below |

> **Note:** If you specifically want to flash an RK3568 board via a Linux VM, prefer `/rk3568-flash vbox-linux full|parts|auto`. It's the dedicated, reproducible path for that hardware. This skill is for the cases that *don't* have a dedicated wrapper yet.

## Known issues

See `references/known-issues.md`. Highlights:

- **Device stuck in `Held` state after `controlvm usbdetach`.** Recovery: drive a mode transition through the device (`adb reboot loader` from inside the VM, or `rkdeveloptool rd`), or physically replug, or (admin) `Restart-Service VBoxUSBMon`.
- **First boot is slow (~3-5 min)** because of cloud-init + emulated AHCI. `up.sh --wait=300` accommodates this.
- **NIC MAC must match cloud-init's netplan seal.** `setup_vbox.sh` generates a random one and persists it to `~/.config/vbox-linux-debug/env`; if you rebuild the VDI, regenerate netplan inside the guest first.

## Extending — adding a new subcommand (jtag, openocd, etc.)

1. Drop a new script under `scripts/<subcmd>.sh`.
2. Source `../lib/config.sh` for shared helpers (`vm_running`, `ssh_vm`, `wait_ssh`, `VBLD_VBOX_BIN`, etc.).
3. Compose the primitives:
   - host-side device prep (`adb reboot ...`, `usb.sh add ...`)
   - wait for snatch (`for _ in $(seq 1 30); do ssh_vm 'lsusb | grep -q VID:PID' && break; sleep 2; done`)
   - `scp_to_vm` if the tool needs an input file
   - `ssh_vm '<linux-tool> <args>'` to actually run
4. Document the new subcommand in this file's "Sub-commands" section.

The point of this skill is the **primitive layer** (VM + USB filter + SSH); domain workflows compose it.
