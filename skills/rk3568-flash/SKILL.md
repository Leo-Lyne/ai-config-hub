---
name: rk3568-flash
description: |
  Flash Rockchip RK3568 boards (any BSP) — write Android/Linux images to eMMC/NAND via Rockchip's upgrade_tool. **Use this skill any time the user wants to put new images onto an RK3568 board**, including: 烧录/烧/刷机/刷固件/写入 boot/uboot/dtbo/super/recovery/parameter/MiniLoader/whole IMAGES, "flash boot.img", "burn the firmware", "reflash my dev board", "刷一下 boot 分区", "把刚编译的 super.img 烧到板子上", "全镜像重烧", "factory reset and reflash", as well as bricked-device rescue via Maskrom (UPDATE button) → MiniLoaderAll → DI parameter → DI partitions → reset. ALSO trigger when the user just rebuilt the kernel / dtbo / Android super partition and wants the changes onto hardware (`./build.sh -K` then "now flash it", or "I changed init.rc, push it to the device"). ALSO trigger when the user is in MaskRom / Loader (PID 2207:350a/350b) and asks how to recover, OR when Windows-side upgrade_tool.exe doesn't see the device and they want a Linux-based fallback. Two transports — `windows` (default, runs upgrade_tool.exe via WSL→Windows interop) and `vbox-linux` (runs Linux upgrade_tool inside a headless VirtualBox VM with USB pass-through; use on Linux-only hosts, for reproducible CI, or when the Windows driver path is broken). Three subcommands per transport — `full` (whole-image factory flash with official UL+DI sequence), `parts <p1> [p2]…` (single/multi-partition fast flash), `auto` (mtime-diff incremental — re-flash only what changed). Auto-detects the BSP repo from $PWD (`build.sh` + `RKTools/` markers), auto-detects upgrade_tool.exe / Linux v2.4 binary, auto-enters Loader via `adb reboot loader`. SKIP this skill only when the user wants to compile/build images (use the BSP build system), debug DTS syntax, dump partition contents, or read UART logs (use /uart-debug). Works with any Rockchip RK3568 BSP repo — zero hardcoded paths, all config via env vars or auto-detection.
---

# RK3568 Flash

Flash images to Rockchip RK3568 boards. Works with any BSP repo that has the
standard layout (`build.sh` + `RKTools/` at the root).

## Quick start

```bash
cd /path/to/your/rk3568-bsp        # any repo with build.sh + RKTools/
/rk3568-flash status               # show resolved config + device state
/rk3568-flash full                 # whole-image factory flash (default: windows transport)
/rk3568-flash parts boot dtbo      # only flash boot + dtbo from rockdev/
/rk3568-flash auto                 # mtime-diff: re-flash only what changed
```

For the alternative transport (Linux upgrade_tool inside a VBox VM):

```bash
/rk3568-flash vbox-linux setup     # one-time provisioning (downloads VBox+cloud-image, builds VM)
/rk3568-flash vbox-linux full      # same as `full` but via VBox VM
/rk3568-flash vbox-linux parts boot
```

## CLI shape

```
/rk3568-flash [<transport>] <subcommand> [args]

transports:  windows (default) | vbox-linux
subcommands: status | full | parts <p>... | auto | setup
```

| Transport | Where the flasher runs | When to use |
|---|---|---|
| `windows` | upgrade_tool.exe natively on Windows host (WSL→Windows interop) | Default. Fast, proven, zero overhead. Requires WSL2 + Rockchip USB driver on Windows. |
| `vbox-linux` | Linux upgrade_tool v2.4 inside a headless VBox VM that owns the USB device via VBox USB filter | Use when Windows USB driver is broken / unavailable, or you're on a non-WSL Linux host, or want a reproducible flashing env. Requires VirtualBox 7+. |

## Configuration (zero hardcoded paths)

All paths are auto-detected; override via environment variables. See
`lib/config.sh` for the full list. Most common:

| Env var | Default | What |
|---|---|---|
| `RK_BSP_ROOT` | walk up from $PWD looking for `build.sh + RKTools/` | Project root |
| `RK_IMAGE_DIR` | newest `<bsp>/IMAGE/*/IMAGES/` | Full-flash IMAGES dir |
| `RK_ROCKDEV_DIR` | newest `<bsp>/rockdev/Image-*/` | Single-partition images dir |
| `RK_UTOOL_EXE` | `<bsp>/RKTools/windows/win_upgrade_tool_v*/upgrade_tool.exe` | Windows flasher |
| `RK_UTOOL_LINUX` | `<bsp>/RKTools/linux/.../upgrade_tool` (auto-extract from .zip if needed); fallback to `deps/linux_upgrade_tool/upgrade_tool` | Linux flasher |
| `RK_VBOX_VM_NAME` | `rk-burn` | VBox VM name |
| `RK_VBOX_SSH_USER` | `$USER` | SSH user inside the VM (must match cloud-init seed) |
| `RK_VBOX_SSH_KEY` | `$HOME/.ssh/id_ed25519` | SSH key for VM access |
| `RK_WIN_CACHE_LX` / `_W` | `/mnt/d/rk3568-flash/cache` / `D:\rk3568-flash\cache` | Windows-accessible staging dir |

To see what got resolved:
```bash
/rk3568-flash status
```

## Subcommands

### `status`
Print resolved config + tool detection + device visibility (per transport). No
side effects. Use this first to verify your environment.

### `full [<dir>]`
Whole-image factory flash. Follows the official Rockchip sequence:

1. `UL MiniLoaderAll.bin -noreset` (refresh Loader to RAM — required for >7 MiB writes)
2. `DI -p parameter.txt` (write GPT)
3. `DI -<part> <part>.img` for every partition listed in `parameter.txt` (skipping userdata)
4. `EL <userdata-LBA> 0x40000` (erase first 128 MiB of userdata so Android rebuilds /data)
5. `RD` (reset device)

`<dir>` defaults to the newest `<bsp>/IMAGE/*/IMAGES/`. Pass an explicit dir or
an `update.img` file to override.

### `parts <p1> [p2]...`
Single/multi-partition fast flash from `<bsp>/rockdev/Image-*/`. Refreshes the
Loader via `UL` first (same reason as above), then `DI -<part>` per arg.

`userdata` flash requires typing `YES` to confirm (data loss).

### `auto`
mtime-diff: scans `<bsp>/rockdev/Image-*/`, compares against the last flash
baseline, and re-flashes only the partitions whose `.img` mtime is newer.

State stored at `${XDG_CACHE_HOME:-$HOME/.cache}/rk3568-flash/last_flash.json`.

### `setup` (vbox-linux only)
Idempotent provisioning for the vbox-linux transport. Installs VirtualBox
(via winget) + Extension Pack + downloads jammy cloud image + builds VM disk +
configures USB filters + runs cloud-init for SSH. Re-run safely.

See `deps/README.md` for what gets bundled vs downloaded.

## Architecture

```
flash.sh  <transport>  <subcommand>  [args]
   │
   ├─→ lib/config.sh      ← all paths auto-detect or read from env
   ├─→ scripts/transports/windows.sh     transport_full / parts / auto / setup / status
   └─→ scripts/transports/vbox_linux.sh  transport_full / parts / auto / setup / status
                                          ↳ delegates provisioning to deps/setup_vbox.sh
```

Both transports implement the same 5-function interface (`transport_full`,
`transport_parts`, `transport_auto`, `transport_setup`, `transport_status`) so
the dispatcher in `flash.sh` is trivial.

## Known issues

See `references/known-issues.md`. Highlights:

- **Don't use the v1.54 upgrade_tool from `rkbin/tools/`** — it's an SDK build
  helper with a buggy verify path that aborts mid-flash. Use the v2.4 in
  `RKTools/linux/Linux_Upgrade_Tool/` (auto-extracted from zip if needed).
- **Always `UL MiniLoaderAll.bin` before `DI`** — without it, the in-flash
  Loader's tiny RAM buffer truncates large writes (~7 MiB). The skill does this
  automatically.
- **Loader RL (read) is restricted to first 32 MiB** of eMMC by Rockchip
  security policy. **Do not trust read-back MD5 to verify a write** — the read
  cap is a feature, not a bug. Verify with UART log + `adb getprop` instead.
- **VBox `Held` state after `usbdetach`** — recovery is `adb reboot loader` (lets
  the device re-enumerate USB cleanly). See known-issues.md.

## Extending

To add a third transport (e.g. native Linux on a host with libusb):

1. Drop `scripts/transports/<name>.sh` defining `transport_full`, `transport_parts`,
   `transport_auto`, `transport_setup`, `transport_status`.
2. Source `_common.sh` for the shared helpers (parameter parsing, adb checks).
3. The dispatcher (`flash.sh`) auto-discovers it (matches `<transport>` arg to
   `transports/<transport>.sh`, with `-` mapped to `_`).

That's it — the rest of the skill is transport-agnostic.
