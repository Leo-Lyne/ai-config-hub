# Known issues — vbox-linux-debug

Generic VM/USB-pass-through issues. Domain-specific gotchas (RK3568 loader
quirks, MCU flash protocol details, etc.) live in their own domain skills.

## 1. Device stuck in `Held` state after `controlvm usbdetach`

After detaching a USB device from the running VM, the device shows
`Current State: Held` in `VBoxManage list usbhost`. Subsequent `usbattach` returns
`busy with a previous request`. Removing the USB filter and power-cycling the
VM does **not** release it.

**Recovery (in order of preference):**
1. Drive a mode transition through the device itself (e.g. `adb reboot loader`
   from inside the VM if the device speaks ADB; `rkdeveloptool rd` if in Loader;
   any tool that re-enumerates the device). Each cleanly trips a fresh VBox
   snatch via the matching filter.
2. Physically replug the USB cable.
3. (Admin) `Restart-Service VBoxUSBMon -Force` from elevated PowerShell.
4. Kill `VBoxSVC.exe` (user-level, no admin) — only safe when no VM is running.

**Avoid manual `controlvm usbdetach` mid-debug** — it's the trigger.

## 2. First boot of the cloud image under VBox is slow (~3-5 min to login prompt)

`up.sh` blocks waiting for SSH for 3-5 minutes on the first VBox boot of an
Ubuntu cloud image. Cloud-init runs once on first boot and fingerprints
hardware. Subsequent boots are normal.

The default 300 s SSH timeout in `up.sh` is sized for this. If you hit it, run
`bash up.sh --wait=600` once.

## 3. NIC name / MAC sealed in netplan

Cloud-init's network-config matches by NIC name pattern (`en*`) by default —
this skill's `network-config.tmpl` does not pin a MAC. So you can rebuild the
VDI without regenerating netplan.

That said, if you previously ran a different cloud image that **did** seal a
specific MAC, and you imported the VDI here, the new VBox-generated MAC won't
match. Either set `VBLD_VM_NIC_MAC` to the original or boot once into rescue
mode and remove the stale `/etc/netplan/*.yaml` so cloud-init regenerates.

`setup_vbox.sh` randomizes a stable MAC on first VM creation and persists it to
`~/.config/vbox-linux-debug/env` so subsequent runs match.

## 4. VBoxManage.exe cannot read WSL ext4 paths

If you set `VBLD_VM_DIR_LX=/home/<user>/VBoxVMs` on WSL2, `VBoxManage.exe`
silently fails to open the VDI. The disk path **must** be under `/mnt/<drive>/`
so the Windows side sees it.

`config.sh` auto-detects this — defaults try `/mnt/d/VBoxVMs` → `/mnt/c/VBoxVMs`
→ `/mnt/c/Users/Public/VBoxVMs`. Override only if you have a specific drive
preference.

## 5. winget hangs silently when called from WSL2

`/mnt/c/Windows/System32/winget.exe install ...` sometimes hangs on the EULA
prompt even with `--accept-package-agreements --accept-source-agreements`.
If `setup_vbox.sh`'s VBox auto-install path appears stuck, kill it and run the
winget command in an interactive Windows PowerShell once — subsequent silent
runs work.

## 6. cloud-init only runs ONCE per `instance-id`

If you change `user-data.tmpl` after the first boot and want it re-applied,
either:

1. Bump `INSTANCE_ID` in the seed (rebuild the seed.iso — `setup_vbox.sh`
   does this automatically when `seed.iso` is missing).
2. SSH in and run `sudo cloud-init clean && sudo cloud-init init` then reboot.

The current `setup_vbox.sh` regenerates `INSTANCE_ID` from `date +%s` whenever
the seed.iso is missing, so deleting the seed forces re-application on next boot.
