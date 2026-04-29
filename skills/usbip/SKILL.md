---
name: usbip
description: Bridge USB devices from the Windows host into WSL2 via usbipd-win. Use this skill WHENEVER the user is in WSL2 and `/dev/ttyUSB*` or `/dev/ttyACM*` is missing, `adb devices` returns empty despite a cable plugged in, Rockchip Rockusb (burn mode) needs to be reached from WSL, or the user mentions usbip / usbipd / "attach USB" / "透传 USB" / 连板子 / 连串口. Also use when the `adb` or `uart-debug` skills fail with "no device" — the root cause is usually that the device isn't passed through to WSL yet. Handles list / attach / detach / status with auto-classification (serial, adb, rockusb) by device name, so no manual BUSID hunting is needed.
---

# USB/IP Bridge (WSL2 ↔ Windows Host)

Make USB devices plugged into the Windows host visible inside WSL2 via `usbipd-win`. All operations use the Python script:

```
SCRIPT="$HOME/.claude/skills/usbip/scripts/usbip_tool.py"
```

## Why this exists

WSL2 runs in a lightweight VM, so USB devices attached to the Windows host are **not** visible inside it by default. `usbipd-win` (Microsoft's tool) exposes selected USB devices over USB/IP so WSL can claim them. The flow is always:

1. On Windows: **bind** the device (requires admin; UAC prompts once per device) → `Shared`
2. On Windows: **attach** it to WSL → shows up as `/dev/ttyUSB*`, `/dev/ttyACM*`, an `adb` device, etc.

This skill wraps `usbipd.exe` (callable from WSL via Windows PATH inheritance) and auto-identifies embedded-development devices by name — CH340 / CP210x / FT232 serial, Android ADB, Rockchip Rockusb — so the user doesn't have to hunt BUSIDs.

## Prerequisites

- WSL2 (this skill is a no-op on native Linux).
- `usbipd-win` on the Windows host. Easiest path:

    ```bash
    bash $HOME/.claude/skills/usbip/deps/fetch_deps.sh        # download MSI into deps/packages/
    bash $HOME/.claude/skills/usbip/deps/install_windows.sh   # silent install via UAC
    ```

  Or `winget install usbipd` from Windows. Open a fresh WSL terminal afterwards
  so Windows `PATH` inheritance picks up `usbipd.exe`.

## Sub-commands

| User says | Run |
|---|---|
| `/usbip list` | `python3 $SCRIPT list` |
| `/usbip status` | `python3 $SCRIPT status` |
| `/usbip attach serial` | `python3 $SCRIPT attach serial` |
| `/usbip attach uart` | `python3 $SCRIPT attach uart` (alias of `serial`) |
| `/usbip attach adb` | `python3 $SCRIPT attach adb` |
| `/usbip attach rockusb` | `python3 $SCRIPT attach rockusb` |
| `/usbip attach all` | `python3 $SCRIPT attach all` |
| `/usbip attach 2-1` | `python3 $SCRIPT attach 2-1` (specific BUSID) |
| `/usbip detach serial` | `python3 $SCRIPT detach serial` |
| `/usbip detach all` | `python3 $SCRIPT detach all` |

## Device classification

The script tags each device as one of:

| Class | Matches on (device-name keywords, case-insensitive) |
|---|---|
| `serial` | `CH340`, `CH341`, `CP210x`, `FT232`, `PL2303`, `SERIAL`, `UART`, `COM\d+` |
| `adb` | `Android`, `ADB` (covers `Android ADB Interface`, `Android Bootloader Interface`, …) |
| `rockusb` | `Rockusb`, `Rockchip` (MaskROM / Loader mode, i.e. firmware burn mode) |
| `other` | anything else |

Matching is on the Windows-side device name string from `usbipd list`, so it covers most vendors without maintaining a VID:PID whitelist.

## `list` output explained

Two sections:

- **Connected** — devices physically plugged into the host right now. The `STATE` column:
  - `Not shared` — never bound (bind with admin required before attach can work)
  - `Shared` — bound, but not currently attached to any WSL distribution
  - `Attached` — currently live inside WSL (should be in `/dev/*` or `adb devices`)
- **Persisted** — previously bound devices that aren't plugged in right now. This is how you notice "ADB was here at some point, but the cable is out now" without grepping through history.

## `attach` behavior

For each connected device that matches the target:

1. If already `Attached` → skip with a note.
2. If `Not shared` → run `usbipd bind --busid <id>` (first-time bind; UAC prompts on the Windows side).
3. Run `usbipd attach --wsl --busid <id>`.
4. Wait briefly for udev, then verify:
   - `serial` → list `/dev/ttyUSB*` + `/dev/ttyACM*`
   - `adb` → `adb devices`

If verification comes back empty but attach reported success, wait 1–2 more seconds and run `/usbip status` — udev can be slow on first enumeration.

## Common pitfalls

- **ADB device is in Persisted but not Connected.** The board's data cable (Type-C/OTG) is charge-only, or not plugged into the PC, or the board isn't booted, or USB gadget isn't configured for ADB. Verify on board side: `getprop sys.usb.config` should contain `adb`.
- **Bind UAC prompt doesn't appear.** First-ever bind of a device requires Windows admin. If no UAC prompt fires, open an admin PowerShell manually and run `usbipd bind --busid <id>`, then `/usbip attach` again. After that initial bind, the device stays `Shared` across reboots — you won't need admin again.
- **WSL reboot drops all attachments.** Not automatic. After `wsl --shutdown` or a Windows restart, re-run `/usbip attach <target>`. For always-on attachment across WSL reboots, use `usbipd attach --auto-attach --wsl --busid <id>` from Windows.
- **Board reboot changes the ADB BUSID.** The BUSID is tied to the physical USB port/hub, not to the device identity. After the board re-enumerates, `/usbip attach adb` finds the new BUSID by class and re-attaches — you don't need to edit anything.
- **Attach succeeds but `/dev/ttyUSB*` never appears.** Rare — usually a driver mismatch. Check `dmesg | tail` inside WSL; the serial chip should log `cdc_acm`, `ch341`, `cp210x`, or `ftdi_sio` binding.

## Error handling

| Error message | Meaning | Fix |
|---|---|---|
| `usbipd.exe not found on PATH` | Not installed, or WSL Path inheritance off | `winget install usbipd` on Windows; restart WSL |
| `bind failed` | UAC denied, or admin required and no prompt fired | Run `usbipd bind --busid <id>` in admin PowerShell |
| `attach failed: ... not shared` | STATE still `Not shared` after bind attempt | Retry bind and accept UAC; then retry attach |
| `No connected device matches '<target>'` | Device unplugged, or classified differently than expected | `/usbip list` to see what's actually connected; pass a BUSID explicitly if needed |

## Tips

- Once a device is bound (`Shared`), the bind survives Windows reboots — only `attach` needs to be re-run after WSL shutdown.
- When debugging "is it a software or a hardware problem?" for a board that just stopped showing up, `/usbip list` is the fastest single-command check: if the device isn't in Connected, it's a physical-layer issue (cable, port, power); if it's Connected but not Attached, it's a WSL pass-through issue.
- `/usbip attach all` is safe when the only shareable devices you've ever bound are your dev-board peripherals — it won't touch devices that are still `Not shared`.
