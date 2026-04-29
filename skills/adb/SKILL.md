---
name: adb
description: "ADB debugging tool for Android BSP development and app debugging. Use when the user says /adb, or asks to run adb commands, capture logcat, install APK, push/pull files, take screenshots, check device info, get/set system properties, reboot device, remount partitions, simulate input, manage permissions, or any Android device interaction via ADB. Covers both low-level BSP debugging (dmesg, remount, props) and app-level debugging (install, logcat, screenshot, input)."
---

# ADB Debugging Tool

Android Debug Bridge tool for BSP development and app debugging.

All operations use: `scripts/adb_tool.py` (relative to this skill directory).

```
SCRIPT="$HOME/.claude/skills/adb/scripts/adb_tool.py"
```

## First-time setup

Bundle Android platform-tools into `deps/platform-tools/` so this skill is
self-contained (no `apt install android-tools-adb` required):

```bash
bash $HOME/.claude/skills/adb/deps/fetch_deps.sh
```

Resolution order in `adb_tool.py`: `$ADB_BIN` → bundled `deps/platform-tools/adb`
→ system `$(command -v adb)` → error.

## Sub-commands Overview

### BSP / 底层调试

| User says | Run |
|---|---|
| `/adb devices` | `python3 $SCRIPT devices` |
| `/adb info` | `python3 $SCRIPT info` |
| `/adb shell "cat /proc/cmdline"` | `python3 $SCRIPT shell cat /proc/cmdline` |
| `/adb dmesg` | `python3 $SCRIPT dmesg` |
| `/adb dmesg -n 100` | `python3 $SCRIPT dmesg -n 100` |
| `/adb props ro.board.platform` | `python3 $SCRIPT props ro.board.platform` |
| `/adb props --set persist.sys.usb.config mtp,adb` | `python3 $SCRIPT props --set persist.sys.usb.config mtp,adb` |
| `/adb props --filter camera` | `python3 $SCRIPT props --filter camera` |
| `/adb remount` | `python3 $SCRIPT remount` |
| `/adb push out/system.img /sdcard/` | `python3 $SCRIPT push out/system.img /sdcard/` |
| `/adb pull /vendor/lib64/hw/camera.so` | `python3 $SCRIPT pull /vendor/lib64/hw/camera.so` |
| `/adb reboot bootloader` | `python3 $SCRIPT reboot bootloader` |

### Logcat / 日志

| User says | Run |
|---|---|
| `/adb log` | `python3 $SCRIPT log` (5s capture) |
| `/adb log --tag CameraService` | `python3 $SCRIPT log -t CameraService` |
| `/adb log --level E` | `python3 $SCRIPT log -l E` (errors only) |
| `/adb log --grep "crash\|fatal"` | `python3 $SCRIPT log -g "crash\|fatal"` |
| `/adb log --lines 100` | `python3 $SCRIPT log -n 100` |
| `/adb log --clear` | `python3 $SCRIPT log --clear` |
| `/adb record-log` | `python3 $SCRIPT record-log` (background) |
| `/adb record-log --stop` | `python3 $SCRIPT record-log --stop` |

### App 调试

| User says | Run |
|---|---|
| `/adb install app.apk` | `python3 $SCRIPT install app.apk` |
| `/adb install -r -g app.apk` | `python3 $SCRIPT install -r -g app.apk` |
| `/adb uninstall com.example.app` | `python3 $SCRIPT uninstall com.example.app` |
| `/adb packages --filter camera` | `python3 $SCRIPT packages -f camera` |
| `/adb packages -3` | `python3 $SCRIPT packages -3` (third-party) |
| `/adb screenshot` | `python3 $SCRIPT screenshot` |
| `/adb activity com.app/.MainActivity` | `python3 $SCRIPT activity com.app/.MainActivity` |
| `/adb input tap --x 500 --y 800` | `python3 $SCRIPT input tap --x 500 --y 800` |
| `/adb input key BACK` | `python3 $SCRIPT input key --keycode BACK` |
| `/adb input text "hello"` | `python3 $SCRIPT input text --text "hello"` |
| `/adb perm com.app --list` | `python3 $SCRIPT perm com.app --list` |
| `/adb perm com.app --grant android.permission.CAMERA` | `python3 $SCRIPT perm com.app --grant android.permission.CAMERA` |

## Global Options

| Option | Example | Description |
|---|---|---|
| `-s`, `--serial` | `-s 71b9bc8b` | Specify device serial (auto-detect if only one device) |

## record-log Details

Similar to `/uart-debug record`:
- Log file: `./adb_logs/<device>-logcat-<YYYY_MMDD_HHMM>.log`
- Start/end markers with timestamps
- Runs as background daemon
- Stop with `record-log --stop`

## Error Handling

| Error | Response |
|---|---|
| No device | Check USB connection and USB debugging enabled |
| Multiple devices | Use `-s <serial>` to specify |
| Permission denied | Script auto-runs `adb root` for BSP commands |
| Remount failed | May need `adb disable-verity` then reboot first |
