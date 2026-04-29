#!/usr/bin/env python3
"""
usbip_tool.py — Bridge USB devices from Windows host into WSL2 via usbipd-win.

Subcommands:
  list              List USB devices on the Windows host, classified
  attach <target>   Attach device(s) to WSL. target = serial|uart|adb|rockusb|all|<BUSID>
  detach <target>   Detach device(s). target = serial|uart|adb|rockusb|all|<BUSID>
  status            Show what's currently attached to WSL

Behavior:
- Auto-binds devices in 'Not shared' state before attaching (bind requires Windows admin; UAC will prompt).
- After attach, verifies by checking /dev/ttyUSB*, /dev/ttyACM*, or `adb devices`.
- Matches devices by Windows device-name keywords (CH340, Android ADB Interface, Rockusb, etc.)
  so it works across many vendors without a VID:PID whitelist.
"""

from __future__ import annotations
import glob
import re
import shutil
import subprocess
import sys
import time


# ─── Device classification profiles ──────────────────────────────────────────

PROFILES = {
    "serial": {
        "name_patterns": [
            r"\bSERIAL\b", r"\bUART\b", r"\bCH340\b", r"\bCH341\b",
            r"\bCP210\d\b", r"\bFT232\b", r"\bPL2303\b", r"\bCOM\d+\b",
        ],
        "display": "Serial / UART",
    },
    "adb": {
        # Windows labels Android USB composite interfaces as "Android ADB Interface"
        # or "Android Bootloader Interface". Fastboot / bootloader count as adb-adjacent here.
        "name_patterns": [r"Android", r"\bADB\b"],
        "display": "Android ADB",
    },
    "rockusb": {
        # Rockchip MaskROM / Loader mode (for firmware burning)
        "name_patterns": [r"Rockusb", r"Rockchip"],
        "display": "Rockchip Rockusb (burn mode)",
    },
}


# ─── usbipd.exe wrapper ──────────────────────────────────────────────────────

def _usbipd(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    """Invoke usbipd.exe (installed on Windows host, callable from WSL)."""
    if shutil.which("usbipd.exe") is None:
        print("[ERROR] usbipd.exe not found on PATH.", file=sys.stderr)
        print("[HINT] Install usbipd-win on the Windows host:", file=sys.stderr)
        print("       winget install usbipd", file=sys.stderr)
        sys.exit(2)
    try:
        r = subprocess.run(
            ["usbipd.exe", *args], capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        print(f"[ERROR] usbipd.exe {' '.join(args)} timed out.", file=sys.stderr)
        sys.exit(2)


def parse_list() -> list[dict]:
    """Parse `usbipd.exe list` output.

    Returns a list of dicts with keys:
      section: 'connected' | 'persisted'
      busid, vidpid, device, state     (connected)
      guid, device                     (persisted)
    """
    rc, out, err = _usbipd("list")
    if rc != 0:
        print(f"[ERROR] usbipd list failed: {err.strip() or out.strip()}", file=sys.stderr)
        sys.exit(2)

    devices: list[dict] = []
    section = None
    # Connected row: BUSID  VID:PID  DEVICE...  STATE   (STATE is one of these literals)
    connected_re = re.compile(
        r"^(\S+)\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\s+(.+?)\s{2,}(Not shared|Shared|Attached)\s*$"
    )
    # Persisted row: GUID(36)  DEVICE...
    persisted_re = re.compile(r"^([0-9a-f\-]{36})\s+(.+?)\s*$")

    for raw in out.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("Connected:"):
            section = "connected"; continue
        if line.startswith("Persisted:"):
            section = "persisted"; continue
        if line.lstrip().startswith(("BUSID", "GUID")):
            continue  # header

        if section == "connected":
            m = connected_re.match(line)
            if m:
                devices.append({
                    "section": "connected",
                    "busid": m.group(1),
                    "vidpid": m.group(2),
                    "device": m.group(3).strip(),
                    "state": m.group(4),
                })
        elif section == "persisted":
            m = persisted_re.match(line)
            if m:
                devices.append({
                    "section": "persisted",
                    "guid": m.group(1),
                    "device": m.group(2).strip(),
                })
    return devices


# ─── Classification ──────────────────────────────────────────────────────────

def classify(dev: dict) -> str:
    name = dev.get("device", "")
    for key, prof in PROFILES.items():
        for pat in prof["name_patterns"]:
            if re.search(pat, name, re.IGNORECASE):
                return key
    return "other"


def _target_matches(dev: dict, target: str) -> bool:
    """Does this connected device match the user's target keyword or BUSID?"""
    if target == "all":
        return True
    if target == dev.get("busid"):
        return True
    aliases = {"uart": "serial"}
    t = aliases.get(target, target)
    return classify(dev) == t


# ─── Verification ────────────────────────────────────────────────────────────

def _verify(devices: list[dict]) -> None:
    """Best-effort verification that the kernel enumerated the attached devices."""
    time.sleep(1.2)  # give udev a moment
    print("\nVerification:")
    classes = {classify(d) for d in devices}

    if "serial" in classes:
        ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
        print(f"  serial: /dev/ttyUSB* = {ports or '(none yet — wait a moment and re-run status)'}")

    if "adb" in classes:
        if shutil.which("adb"):
            try:
                out = subprocess.check_output(["adb", "devices"], text=True, timeout=5)
                lines = [l for l in out.splitlines()[1:] if l.strip()]
                if lines:
                    print("  adb devices:")
                    for l in lines:
                        print(f"      {l}")
                else:
                    print("  adb: no devices listed yet — try again in a second, or unplug/replug the data cable.")
            except Exception as e:
                print(f"  adb: query failed ({e})")
        else:
            print("  adb: `adb` not installed in this WSL — skipping check.")


# ─── Sub-commands ────────────────────────────────────────────────────────────

def cmd_list() -> None:
    devs = parse_list()
    connected = [d for d in devs if d["section"] == "connected"]
    persisted = [d for d in devs if d["section"] == "persisted"]

    if connected:
        print(f"{'BUSID':6} {'VID:PID':10} {'CLASS':9} {'STATE':12} DEVICE")
        print("-" * 90)
        for d in connected:
            print(f"{d['busid']:6} {d['vidpid']:10} {classify(d):9} {d['state']:12} {d['device']}")
    else:
        print("(no USB devices currently connected to the Windows host)")

    if persisted:
        print("\nPersisted (previously bound, not currently plugged in):")
        for d in persisted:
            cls = classify(d)
            print(f"  [{cls}] {d['device']}")


def cmd_attach(target: str) -> None:
    devs = parse_list()
    matches = [d for d in devs if d["section"] == "connected" and _target_matches(d, target)]

    if not matches:
        _attach_no_match_hints(target, devs)
        sys.exit(1)

    for d in matches:
        busid, name, state = d["busid"], d["device"], d["state"]
        print(f"[{busid}] {name}  ({state})")

        if state == "Attached":
            print("  ✓ already attached")
            continue

        if state == "Not shared":
            print("  → binding (needs Windows admin; UAC may prompt)...")
            rc, out, err = _usbipd("bind", "--busid", busid)
            if rc != 0:
                print(f"  ✗ bind failed: {(err or out).strip()}")
                print(f"  [HINT] From Windows admin PowerShell run:  usbipd bind --busid {busid}")
                continue
            print("  ✓ bound")

        print("  → attaching to WSL...")
        rc, out, err = _usbipd("attach", "--wsl", "--busid", busid)
        if rc != 0:
            print(f"  ✗ attach failed: {(err or out).strip()}")
            continue
        print("  ✓ attached")

    _verify(matches)


def _attach_no_match_hints(target: str, devs: list[dict]) -> None:
    """Explain why no device matched — the #1 gotcha is device not being plugged in."""
    print(f"[WARN] No connected device matches target '{target}'.")

    if target in {"adb", "android"}:
        persisted_adb = [d for d in devs if d["section"] == "persisted" and classify(d) == "adb"]
        if persisted_adb:
            print("[HINT] Android ADB Interface exists in Persisted history but isn't currently")
            print("       visible on the host. Check:")
            print("         1. Type-C cable plugged into the PC's USB port (not just a charger)")
            print("         2. The cable supports data (many charge-only cables look identical)")
            print("         3. Board is booted and USB debugging is enabled")
            print("         4. `getprop sys.usb.config` on the board shows 'adb'")

    print("\nRun 'list' to see what IS connected.")


def cmd_detach(target: str) -> None:
    devs = parse_list()
    matches = [
        d for d in devs
        if d["section"] == "connected" and d["state"] == "Attached" and _target_matches(d, target)
    ]
    if not matches:
        print(f"[INFO] No attached device matches '{target}'.")
        return
    for d in matches:
        busid, name = d["busid"], d["device"]
        rc, out, err = _usbipd("detach", "--busid", busid)
        if rc == 0:
            print(f"  ✓ detached [{busid}] {name}")
        else:
            print(f"  ✗ detach failed [{busid}]: {(err or out).strip()}")


def cmd_status() -> None:
    devs = parse_list()
    attached = [d for d in devs if d["section"] == "connected" and d["state"] == "Attached"]
    if not attached:
        print("No USB devices currently attached to WSL.")
        return
    print("Attached to WSL:")
    for d in attached:
        cls = classify(d)
        print(f"  [{d['busid']}] [{cls}] {d['device']}")

    # Quick reality check
    ports = sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
    if any(classify(d) == "serial" for d in attached):
        print(f"  /dev/ttyUSB* + /dev/ttyACM* : {ports or '(none — kernel hasn’t enumerated yet?)'}")
    if any(classify(d) == "adb" for d in attached) and shutil.which("adb"):
        try:
            out = subprocess.check_output(["adb", "devices"], text=True, timeout=5)
            lines = [l for l in out.splitlines()[1:] if l.strip()]
            print("  adb devices:")
            for l in lines or ["  (none)"]:
                print(f"      {l}")
        except Exception:
            pass


# ─── Entry point ─────────────────────────────────────────────────────────────

USAGE = """\
Usage: usbip_tool.py <command> [target]

Commands:
  list                         List host-side USB devices, classified
  attach <target>              Attach: serial | uart | adb | rockusb | all | <BUSID>
  detach <target>              Detach same targets (or 'all')
  status                       Show what's attached right now

Examples:
  usbip_tool.py list
  usbip_tool.py attach serial          # auto-find CH340/CP210x/FT232/etc.
  usbip_tool.py attach adb             # auto-find Android ADB Interface
  usbip_tool.py attach all             # attach every connected shareable device
  usbip_tool.py attach 2-1             # attach a specific BUSID
  usbip_tool.py detach all
"""

def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help", "help"}:
        print(USAGE)
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == "list":
        cmd_list()
    elif cmd == "attach":
        if not arg:
            print("Usage: attach <serial|uart|adb|rockusb|all|BUSID>"); sys.exit(1)
        cmd_attach(arg)
    elif cmd == "detach":
        cmd_detach(arg or "all")
    elif cmd == "status":
        cmd_status()
    else:
        print(f"Unknown command: {cmd}\n"); print(USAGE); sys.exit(1)


if __name__ == "__main__":
    main()
