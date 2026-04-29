#!/usr/bin/env python3
"""
ADB tool for BSP development and Android app debugging.

Sub-commands:
  devices     - List connected devices
  info        - Device info summary
  shell       - Execute shell command
  log         - Capture logcat (filtered)
  dmesg       - Kernel log
  push        - Push file to device
  pull        - Pull file from device
  install     - Install APK
  uninstall   - Uninstall package
  packages    - List installed packages
  screenshot  - Capture screen
  record-log  - Record logcat to file
  props       - Get/set system properties
  reboot      - Reboot device
  remount     - Remount /system /vendor as r/w
  input       - Simulate input (tap/swipe/text/key)
  activity    - Start an activity
  perm        - Manage app permissions
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import json
import signal
import time
from datetime import datetime
from pathlib import Path


# ── ADB binary resolution ─────────────────────────────────────────
# Prefer the bundled platform-tools shipped under deps/, fall back to $PATH.
# Override via $ADB_BIN. Exits early if no adb is found anywhere.

def _resolve_adb_bin() -> str:
    env = os.environ.get("ADB_BIN")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    skill_root = Path(__file__).resolve().parent.parent
    bundled = skill_root / "deps" / "platform-tools" / "adb"
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    sysbin = shutil.which("adb")
    if sysbin:
        return sysbin
    print(
        "[ERROR] adb not found. Bootstrap the bundled platform-tools:\n"
        "        bash $HOME/.claude/skills/adb/deps/fetch_deps.sh\n"
        "  Or override: export ADB_BIN=/path/to/adb",
        file=sys.stderr,
    )
    sys.exit(1)


ADB_BIN = _resolve_adb_bin()


# ── ADB wrapper ───────────────────────────────────────────────────

def adb(*args, device=None, timeout=30, check=True, capture=True):
    """Run an adb command and return stdout."""
    cmd = [ADB_BIN]
    if device:
        cmd += ["-s", device]
    cmd += list(args)

    try:
        r = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=timeout,
        )
        if check and r.returncode != 0:
            stderr = r.stderr.strip() if r.stderr else ""
            if stderr:
                print(f"[ERROR] {stderr}", file=sys.stderr)
        if capture:
            return r.stdout.strip()
        return ""
    except subprocess.TimeoutExpired:
        print(f"[ERROR] Command timed out after {timeout}s: {' '.join(cmd)}", file=sys.stderr)
        return ""
    except FileNotFoundError:
        print("[ERROR] adb not found. Install Android SDK Platform Tools.", file=sys.stderr)
        sys.exit(1)


def resolve_device(device_arg):
    """Resolve device serial. Auto-select if only one connected."""
    if device_arg:
        return device_arg

    out = adb("devices")
    lines = [l for l in out.splitlines()[1:] if l.strip() and "device" in l]
    if len(lines) == 1:
        dev = lines[0].split()[0]
        return dev
    elif len(lines) > 1:
        print("[INFO] Multiple devices:")
        for l in lines:
            print(f"  {l}")
        dev = lines[0].split()[0]
        print(f"[INFO] Using first: {dev}")
        print(f"[HINT] Specify with: -s <serial>")
        return dev
    else:
        print("[ERROR] No device connected.", file=sys.stderr)
        sys.exit(1)


# ── Sub-commands ──────────────────────────────────────────────────

def cmd_devices(args):
    """List connected devices with status."""
    out = adb("devices", "-l")
    print(out)


def cmd_info(args):
    """Device info summary."""
    dev = resolve_device(args.serial)
    props = {
        "Model": "ro.product.model",
        "Brand": "ro.product.brand",
        "Device": "ro.product.device",
        "Platform": "ro.board.platform",
        "SOC": "ro.hardware.chipname",
        "Android": "ro.build.version.release",
        "SDK": "ro.build.version.sdk",
        "Build": "ro.build.display.id",
        "Kernel": None,  # special
        "Serial": "ro.serialno",
        "WiFi IP": None,  # special
        "Uptime": None,  # special
    }

    print(f"[INFO] Device: {dev}\n")
    for label, prop in props.items():
        if prop:
            val = adb("shell", f"getprop {prop}", device=dev)
        elif label == "Kernel":
            val = adb("shell", "uname -r", device=dev)
        elif label == "WiFi IP":
            val = adb("shell", "ip -4 addr show wlan0 2>/dev/null | grep inet | awk '{print $2}'", device=dev)
        elif label == "Uptime":
            val = adb("shell", "uptime -p 2>/dev/null || uptime", device=dev)
        else:
            val = ""
        if val:
            print(f"  {label:12s}: {val}")


def cmd_shell(args):
    """Execute shell command on device."""
    dev = resolve_device(args.serial)
    command = " ".join(args.command)
    if not command:
        print("[ERROR] No command specified.", file=sys.stderr)
        sys.exit(1)
    out = adb("shell", command, device=dev, timeout=args.timeout)
    if out:
        print(out)


def cmd_log(args):
    """Capture logcat output."""
    dev = resolve_device(args.serial)
    cmd = [ADB_BIN]
    if dev:
        cmd += ["-s", dev]
    cmd += ["logcat"]

    if args.clear:
        adb("logcat", "-c", device=dev)
        print("[INFO] Logcat buffer cleared.")
        return

    # Filters
    if args.tag:
        cmd += [f"{args.tag}:{args.level or 'V'}", "*:S"]
    elif args.level:
        cmd += [f"*:{args.level}"]

    if args.grep:
        cmd_str = " ".join(cmd) + f" | grep -iE '{args.grep}'"
    else:
        cmd_str = None

    if args.lines:
        cmd += ["-t", str(args.lines)]

    timeout = args.duration or 5

    print(f"[LOG] Capturing logcat for {timeout}s...")
    if args.tag:
        print(f"[LOG] Filter: tag={args.tag} level={args.level or 'V'}")
    if args.grep:
        print(f"[LOG] Grep: {args.grep}")
    print()

    try:
        if cmd_str:
            proc = subprocess.run(
                cmd_str, shell=True, text=True,
                capture_output=True, timeout=timeout,
            )
        else:
            proc = subprocess.run(
                cmd, text=True,
                capture_output=True, timeout=timeout,
            )
        if proc.stdout:
            print(proc.stdout)
    except subprocess.TimeoutExpired as e:
        if e.stdout:
            text = e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", errors="replace")
            print(text)


def cmd_dmesg(args):
    """Kernel log."""
    dev = resolve_device(args.serial)
    # Try root first for full dmesg
    adb("root", device=dev, check=False)
    time.sleep(0.5)

    tail = args.lines or 50
    out = adb("shell", f"dmesg | tail -n {tail}", device=dev, timeout=10)
    if out:
        print(out)
    else:
        print("[WARN] dmesg empty. Device may need root access.", file=sys.stderr)


def cmd_push(args):
    """Push file to device."""
    dev = resolve_device(args.serial)
    local = args.local
    remote = args.remote

    if not os.path.exists(local):
        print(f"[ERROR] Local file not found: {local}", file=sys.stderr)
        sys.exit(1)

    print(f"[PUSH] {local} -> {remote}")
    out = adb("push", local, remote, device=dev, timeout=120)
    print(out)


def cmd_pull(args):
    """Pull file from device."""
    dev = resolve_device(args.serial)
    remote = args.remote
    local = args.local or os.path.basename(remote)

    print(f"[PULL] {remote} -> {local}")
    out = adb("pull", remote, local, device=dev, timeout=120)
    print(out)


def cmd_install(args):
    """Install APK."""
    dev = resolve_device(args.serial)
    apk = args.apk

    if not os.path.exists(apk):
        print(f"[ERROR] APK not found: {apk}", file=sys.stderr)
        sys.exit(1)

    flags = []
    if args.replace:
        flags.append("-r")
    if args.downgrade:
        flags += ["-r", "-d"]
    if args.grant:
        flags.append("-g")

    print(f"[INSTALL] {apk}")
    out = adb("install", *flags, apk, device=dev, timeout=120)
    print(out)


def cmd_uninstall(args):
    """Uninstall package."""
    dev = resolve_device(args.serial)
    pkg = args.package

    if args.keep_data:
        out = adb("shell", f"pm uninstall -k {pkg}", device=dev)
    else:
        out = adb("uninstall", pkg, device=dev)
    print(out)


def cmd_packages(args):
    """List installed packages."""
    dev = resolve_device(args.serial)
    cmd_parts = "pm list packages"

    if args.system:
        cmd_parts += " -s"
    elif args.third_party:
        cmd_parts += " -3"

    out = adb("shell", cmd_parts, device=dev)
    lines = sorted(out.splitlines())

    if args.filter:
        lines = [l for l in lines if args.filter.lower() in l.lower()]

    for line in lines:
        print(line.replace("package:", "  "))
    print(f"\n[INFO] Total: {len(lines)} packages")


def cmd_screenshot(args):
    """Capture screenshot."""
    dev = resolve_device(args.serial)
    outdir = args.output or "."
    os.makedirs(outdir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"screenshot_{ts}.png"
    local_path = os.path.join(outdir, filename)
    remote_path = f"/sdcard/{filename}"

    print(f"[SCREENSHOT] Capturing...")
    adb("shell", f"screencap -p {remote_path}", device=dev)
    adb("pull", remote_path, local_path, device=dev)
    adb("shell", f"rm {remote_path}", device=dev)
    print(f"[SCREENSHOT] Saved: {local_path}")


def cmd_record_log(args):
    """Record logcat to file."""

    # Stop recording
    if args.stop:
        pid_file = "/tmp/.adb_record_log.json"
        try:
            with open(pid_file) as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            print("[RECORD] No active recording found.")
            return

        pid = state["pid"]
        logfile = state["logfile"]
        start_epoch = state["start_epoch"]

        try:
            os.kill(pid, signal.SIGTERM)
            print(f"[RECORD] Stopped recording process (PID: {pid})")
        except ProcessLookupError:
            print(f"[RECORD] Process {pid} already exited.")

        # Write end marker
        end_time = datetime.now()
        duration_sec = int(time.time() - start_epoch)
        duration_min = duration_sec // 60
        duration_remain = duration_sec % 60

        end_marker = (
            f"\n----------- log-end @ {end_time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"| duration: {duration_min}m{duration_remain}s -----------\n"
        )
        try:
            with open(logfile, "a") as f:
                f.write(end_marker)
            print(f"[RECORD] End marker written to {logfile}")
            print(f"[RECORD] Duration: {duration_min}m{duration_remain}s")
        except Exception as e:
            print(f"[ERROR] Cannot write end marker: {e}", file=sys.stderr)

        os.unlink(pid_file)
        return

    # Start recording
    dev = resolve_device(args.serial)
    log_dir = args.output or "./adb_logs"
    os.makedirs(log_dir, exist_ok=True)

    # Detect platform
    model = adb("shell", "getprop ro.product.device", device=dev) or "unknown"
    ts = datetime.now().strftime("%Y_%m%d_%H%M")
    logfile = os.path.join(log_dir, f"{model}-logcat-{ts}.log")

    # Build logcat command
    logcat_cmd = [ADB_BIN]
    if dev:
        logcat_cmd += ["-s", dev]
    logcat_cmd += ["logcat"]

    if args.tag:
        logcat_cmd += [f"{args.tag}:V", "*:S"]
    if args.level:
        logcat_cmd += [f"*:{args.level}"]

    # Clear buffer before recording
    adb("logcat", "-c", device=dev, check=False)

    print(f"[RECORD] Device:   {dev} ({model})")
    print(f"[RECORD] Log file: {logfile}")

    pid = os.fork()
    if pid == 0:
        os.setsid()
        start_marker = f"----------- log-start @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} -----------\n"

        def _shutdown(_sig, _frame):
            os._exit(0)
        signal.signal(signal.SIGTERM, _shutdown)

        try:
            with open(logfile, "w") as f:
                f.write(start_marker)
                f.flush()
                proc = subprocess.Popen(
                    logcat_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                )
                for line in proc.stdout:
                    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    f.write(f"[{ts_str}] {line}")
                    f.flush()
        except Exception:
            pass
        os._exit(0)
    else:
        pid_file = "/tmp/.adb_record_log.json"
        with open(pid_file, "w") as f:
            json.dump({"pid": pid, "logfile": os.path.abspath(logfile), "start_epoch": time.time()}, f)
        print(f"[RECORD] Recording started (PID: {pid})")
        print(f"[RECORD] Stop with: python3 {__file__} record-log --stop")


def cmd_props(args):
    """Get or set system properties."""
    dev = resolve_device(args.serial)

    if args.set_value:
        # Set property
        prop_name, prop_val = args.set_value
        adb("root", device=dev, check=False)
        time.sleep(0.5)
        out = adb("shell", f"setprop {prop_name} {prop_val}", device=dev)
        print(f"[PROPS] Set {prop_name} = {prop_val}")
        if out:
            print(out)
    elif args.name:
        # Get single property
        out = adb("shell", f"getprop {args.name}", device=dev)
        print(f"{args.name} = {out}")
    elif args.filter:
        # Filter properties
        out = adb("shell", "getprop", device=dev)
        for line in out.splitlines():
            if args.filter.lower() in line.lower():
                print(line)
    else:
        # List all
        out = adb("shell", "getprop", device=dev)
        print(out)


def cmd_reboot(args):
    """Reboot device."""
    dev = resolve_device(args.serial)
    mode = args.mode or "normal"

    mode_map = {
        "normal": [],
        "bootloader": ["bootloader"],
        "recovery": ["recovery"],
        "fastboot": ["bootloader"],
        "edl": ["edl"],
    }

    if mode not in mode_map:
        print(f"[ERROR] Unknown mode: {mode}. Use: normal/bootloader/recovery/fastboot/edl", file=sys.stderr)
        sys.exit(1)

    print(f"[REBOOT] Rebooting to {mode}...")
    reboot_args = ["reboot"] + mode_map[mode]
    adb(*reboot_args, device=dev, check=False)


def cmd_remount(args):
    """Remount /system and /vendor as read-write."""
    dev = resolve_device(args.serial)
    print("[REMOUNT] Requesting root...")
    adb("root", device=dev, check=False)
    time.sleep(1)
    print("[REMOUNT] Disabling verity...")
    out = adb("disable-verity", device=dev, check=False)
    if out:
        print(out)
    print("[REMOUNT] Remounting...")
    out = adb("remount", device=dev, check=False)
    if out:
        print(out)
    print("[REMOUNT] Done. Reboot if verity was just disabled.")


def cmd_input(args):
    """Simulate input on device."""
    dev = resolve_device(args.serial)
    action = args.action

    if action == "tap":
        if not args.x or not args.y:
            print("[ERROR] tap requires --x and --y", file=sys.stderr)
            sys.exit(1)
        adb("shell", f"input tap {args.x} {args.y}", device=dev)
        print(f"[INPUT] Tap at ({args.x}, {args.y})")

    elif action == "swipe":
        if not all([args.x, args.y, args.x2, args.y2]):
            print("[ERROR] swipe requires --x --y --x2 --y2", file=sys.stderr)
            sys.exit(1)
        duration = args.duration or 300
        adb("shell", f"input swipe {args.x} {args.y} {args.x2} {args.y2} {duration}", device=dev)
        print(f"[INPUT] Swipe ({args.x},{args.y}) -> ({args.x2},{args.y2})")

    elif action == "text":
        if not args.text:
            print("[ERROR] text requires --text", file=sys.stderr)
            sys.exit(1)
        # Escape spaces for shell
        escaped = args.text.replace(" ", "%s")
        adb("shell", f"input text '{escaped}'", device=dev)
        print(f"[INPUT] Text: {args.text}")

    elif action == "key":
        if not args.keycode:
            print("[ERROR] key requires --keycode (e.g. BACK, HOME, ENTER, POWER, VOLUME_UP)", file=sys.stderr)
            sys.exit(1)
        keycode = args.keycode if args.keycode.startswith("KEYCODE_") else f"KEYCODE_{args.keycode.upper()}"
        adb("shell", f"input keyevent {keycode}", device=dev)
        print(f"[INPUT] Key: {keycode}")

    else:
        print(f"[ERROR] Unknown action: {action}. Use: tap/swipe/text/key", file=sys.stderr)


def cmd_activity(args):
    """Start an activity."""
    dev = resolve_device(args.serial)
    component = args.component

    extra_args = []
    if args.action:
        extra_args += ["-a", args.action]
    if args.data:
        extra_args += ["-d", args.data]
    if args.category:
        extra_args += ["-c", args.category]

    am_cmd = f"am start {' '.join(extra_args)} {component}" if component else f"am start {' '.join(extra_args)}"
    out = adb("shell", am_cmd, device=dev)
    if out:
        print(out)


def cmd_perm(args):
    """Manage app permissions."""
    dev = resolve_device(args.serial)
    pkg = args.package

    if args.list:
        out = adb("shell", f"dumpsys package {pkg} | grep permission", device=dev)
        if out:
            print(out)
    elif args.grant:
        out = adb("shell", f"pm grant {pkg} {args.grant}", device=dev)
        print(f"[PERM] Granted {args.grant} to {pkg}")
    elif args.revoke:
        out = adb("shell", f"pm revoke {pkg} {args.revoke}", device=dev)
        print(f"[PERM] Revoked {args.revoke} from {pkg}")
    elif args.reset:
        out = adb("shell", f"pm reset-permissions {pkg}", device=dev)
        print(f"[PERM] Reset permissions for {pkg}")
    else:
        # Default: list
        out = adb("shell", f"dumpsys package {pkg} | grep permission", device=dev)
        if out:
            print(out)


# ── CLI entry point ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ADB tool for BSP and Android debugging",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-s", "--serial", help="Device serial (auto-detect if omitted)")

    sub = parser.add_subparsers(dest="cmd", help="Sub-command")

    # devices
    sub.add_parser("devices", help="List connected devices")

    # info
    sub.add_parser("info", help="Device info summary")

    # shell
    p_shell = sub.add_parser("shell", help="Execute shell command")
    p_shell.add_argument("command", nargs="+", help="Command to execute")
    p_shell.add_argument("--timeout", type=int, default=30, help="Timeout seconds (default: 30)")

    # log
    p_log = sub.add_parser("log", help="Capture logcat")
    p_log.add_argument("--tag", "-t", help="Filter by tag")
    p_log.add_argument("--level", "-l", help="Min level: V/D/I/W/E/F")
    p_log.add_argument("--grep", "-g", help="Grep pattern in output")
    p_log.add_argument("--lines", "-n", type=int, help="Last N lines only")
    p_log.add_argument("--duration", type=int, help="Capture duration in seconds (default: 5)")
    p_log.add_argument("--clear", action="store_true", help="Clear logcat buffer")

    # dmesg
    p_dmesg = sub.add_parser("dmesg", help="Kernel log")
    p_dmesg.add_argument("--lines", "-n", type=int, help="Last N lines (default: 50)")

    # push
    p_push = sub.add_parser("push", help="Push file to device")
    p_push.add_argument("local", help="Local file path")
    p_push.add_argument("remote", help="Remote path on device")

    # pull
    p_pull = sub.add_parser("pull", help="Pull file from device")
    p_pull.add_argument("remote", help="Remote path on device")
    p_pull.add_argument("local", nargs="?", help="Local save path (default: current dir)")

    # install
    p_install = sub.add_parser("install", help="Install APK")
    p_install.add_argument("apk", help="APK file path")
    p_install.add_argument("-r", "--replace", action="store_true", help="Replace existing")
    p_install.add_argument("-d", "--downgrade", action="store_true", help="Allow downgrade")
    p_install.add_argument("-g", "--grant", action="store_true", help="Grant all permissions")

    # uninstall
    p_uninstall = sub.add_parser("uninstall", help="Uninstall package")
    p_uninstall.add_argument("package", help="Package name")
    p_uninstall.add_argument("-k", "--keep-data", action="store_true", help="Keep data/cache")

    # packages
    p_packages = sub.add_parser("packages", help="List packages")
    p_packages.add_argument("--filter", "-f", help="Filter by name")
    p_packages.add_argument("--system", action="store_true", help="System packages only")
    p_packages.add_argument("--third-party", "-3", action="store_true", help="Third-party only")

    # screenshot
    p_ss = sub.add_parser("screenshot", help="Capture screenshot")
    p_ss.add_argument("--output", "-o", help="Output directory")

    # record-log
    p_rec = sub.add_parser("record-log", help="Record logcat to file")
    p_rec.add_argument("--stop", action="store_true", help="Stop active recording")
    p_rec.add_argument("--tag", "-t", help="Filter by tag")
    p_rec.add_argument("--level", "-l", help="Min level: V/D/I/W/E/F")
    p_rec.add_argument("--output", "-o", help="Output directory (default: ./adb_logs)")

    # props
    p_props = sub.add_parser("props", help="System properties")
    p_props.add_argument("name", nargs="?", help="Property name to get")
    p_props.add_argument("--set", dest="set_value", nargs=2, metavar=("NAME", "VALUE"), help="Set property")
    p_props.add_argument("--filter", "-f", help="Filter properties by keyword")

    # reboot
    p_reboot = sub.add_parser("reboot", help="Reboot device")
    p_reboot.add_argument("mode", nargs="?", default="normal",
                          help="Mode: normal/bootloader/recovery/fastboot/edl")

    # remount
    sub.add_parser("remount", help="Remount /system /vendor as r/w")

    # input
    p_input = sub.add_parser("input", help="Simulate input")
    p_input.add_argument("action", help="Action: tap/swipe/text/key")
    p_input.add_argument("--x", type=int)
    p_input.add_argument("--y", type=int)
    p_input.add_argument("--x2", type=int, help="Swipe end X")
    p_input.add_argument("--y2", type=int, help="Swipe end Y")
    p_input.add_argument("--duration", type=int, help="Swipe duration ms")
    p_input.add_argument("--text", help="Text to input")
    p_input.add_argument("--keycode", help="Key: BACK/HOME/ENTER/POWER/VOLUME_UP...")

    # activity
    p_act = sub.add_parser("activity", help="Start activity")
    p_act.add_argument("component", nargs="?", help="Component: com.app/.Activity")
    p_act.add_argument("--action", "-a", help="Intent action")
    p_act.add_argument("--data", "-d", help="Intent data URI")
    p_act.add_argument("--category", "-c", help="Intent category")

    # perm
    p_perm = sub.add_parser("perm", help="Manage permissions")
    p_perm.add_argument("package", help="Package name")
    p_perm.add_argument("--list", action="store_true", help="List permissions")
    p_perm.add_argument("--grant", help="Grant permission")
    p_perm.add_argument("--revoke", help="Revoke permission")
    p_perm.add_argument("--reset", action="store_true", help="Reset all permissions")

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        sys.exit(0)

    cmd_map = {
        "devices": cmd_devices, "info": cmd_info, "shell": cmd_shell,
        "log": cmd_log, "dmesg": cmd_dmesg, "push": cmd_push,
        "pull": cmd_pull, "install": cmd_install, "uninstall": cmd_uninstall,
        "packages": cmd_packages, "screenshot": cmd_screenshot,
        "record-log": cmd_record_log, "props": cmd_props, "reboot": cmd_reboot,
        "remount": cmd_remount, "input": cmd_input, "activity": cmd_activity,
        "perm": cmd_perm,
    }
    cmd_map[args.cmd](args)


if __name__ == "__main__":
    main()
