#!/usr/bin/env python3
"""
UART serial communication tool for embedded dev boards.

Sub-commands:
  recv    - Receive and display serial output
  send    - Send a command to the serial port
  record  - Record serial log to a timestamped file
"""

import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# ── Bundled-venv bootstrap ────────────────────────────────────────
# pyserial is the only runtime dep. Try the ambient interpreter first; if
# `import serial` fails, re-exec under deps/.venv/bin/python (created by
# deps/install.sh). Final fallback: print install hint and exit.
def _ensure_pyserial():
    try:
        import serial  # noqa: F401
        return
    except ImportError:
        pass
    skill_root = Path(__file__).resolve().parent.parent
    venv_py = skill_root / "deps" / ".venv" / "bin" / "python"
    if venv_py.is_file() and os.path.realpath(sys.executable) != os.path.realpath(str(venv_py)):
        os.execv(str(venv_py), [str(venv_py), __file__, *sys.argv[1:]])
    print(
        "[ERROR] pyserial not available and bundled venv missing. Run:\n"
        "        bash $HOME/.claude/skills/uart-debug/deps/install.sh",
        file=sys.stderr,
    )
    sys.exit(1)


_ensure_pyserial()
import serial


# ── Device detection ──────────────────────────────────────────────

def find_serial_devices():
    """Auto-detect USB serial devices."""
    patterns = ["/dev/ttyUSB*", "/dev/ttyACM*"]
    devices = []
    for p in patterns:
        devices.extend(glob.glob(p))
    return sorted(devices)


# ── Device/baud cache (avoids repeated auto-detection on every invocation) ──
CACHE_PATH = "/tmp/.uart_cache.json"


def _cache_load():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _cache_save(data):
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(data, f)
    except OSError:
        pass


def resolve_device(device_arg):
    """Resolve device path: explicit arg → cache → auto-detect."""
    if device_arg:
        if not os.path.exists(device_arg):
            print(f"[ERROR] Device not found: {device_arg}", file=sys.stderr)
            _suggest_wsl_fix()
            sys.exit(1)
        return device_arg

    cache = _cache_load()
    cached = cache.get("device")
    if cached and os.path.exists(cached):
        return cached

    devices = find_serial_devices()
    if len(devices) == 1:
        print(f"[INFO] Auto-detected device: {devices[0]}")
        _cache_save({**cache, "device": devices[0]})
        return devices[0]
    elif len(devices) > 1:
        print(f"[INFO] Multiple devices found:")
        for i, d in enumerate(devices):
            print(f"  [{i}] {d}")
        print(f"[INFO] Using first device: {devices[0]}")
        print(f"[HINT] Specify device explicitly: uart.py recv --device /dev/ttyUSB1")
        _cache_save({**cache, "device": devices[0]})
        return devices[0]
    else:
        print("[ERROR] No serial device found.", file=sys.stderr)
        _suggest_wsl_fix()
        sys.exit(1)


def _suggest_wsl_fix():
    """If on WSL2, suggest usbipd."""
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                print("\n[HINT] WSL2 detected. USB devices need usbipd to pass through:")
                print("  (PowerShell admin) usbipd list")
                print("  (PowerShell admin) usbipd attach --wsl --busid <BUSID>")
    except Exception:
        pass


# ── Baud rate auto-detection ──────────────────────────────────────

COMMON_BAUDS = [1500000, 115200, 921600, 460800, 230400, 57600, 38400, 9600]


def detect_baud(device):
    """Return a working baud rate for `device`, using cache when possible.

    Cached value is reused if the cache entry matches `device`. On cache miss,
    try common baud rates by sending CR and checking for printable response.
    """
    # Fast path: cached baud for this device
    cache = _cache_load()
    if cache.get("device") == device and cache.get("baud"):
        return cache["baud"]

    # If a record daemon owns the port we can't open it for probing — read
    # the baud from the daemon's perspective via the cache, or just fall back.
    if running_record_daemons():
        if cache.get("baud"):
            return cache["baud"]
        print("[WARN] record daemon active and no cached baud; falling back to 1500000")
        return 1500000

    print(f"[INFO] Auto-detecting baud rate on {device}...")
    for baud in COMMON_BAUDS:
        try:
            ser = serial.Serial(device, baud, timeout=1)
            ser.reset_input_buffer()
            ser.write(b"\r\n")
            ser.flush()
            time.sleep(0.3)
            data = ser.read(ser.in_waiting or 256)
            ser.close()

            if not data:
                continue

            printable = sum(1 for b in data if 0x20 <= b <= 0x7E or b in (0x0A, 0x0D, 0x09))
            ratio = printable / len(data)
            if ratio > 0.7 and len(data) >= 2:
                print(f"[INFO] Detected baud rate: {baud}")
                _cache_save({**cache, "device": device, "baud": baud})
                return baud
        except (serial.SerialException, OSError):
            continue

    print("[WARN] Could not auto-detect baud rate, falling back to 115200")
    return 115200


def detect_platform():
    """Detect platform name from project directory."""
    cwd = os.path.basename(os.getcwd())
    m = re.search(r'(atk-rk\d+)', cwd, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    # try parent dirs
    for part in Path.cwd().parts:
        m = re.search(r'(atk-rk\d+)', part, re.IGNORECASE)
        if m:
            return m.group(1).lower()
    return "unknown-board"


# ── PID file for record state tracking ────────────────────────────

PID_FILE = "/tmp/.uart_record.json"

# Log rotation threshold. xHCI spam can fill GB/hour on some boards;
# rotating at 100 MB keeps individual files grep-able while unlimited
# recording can still continue via multiple segments.
MAX_LOG_SIZE = int(os.environ.get("UART_MAX_LOG_BYTES", 100 * 1024 * 1024))


def save_record_state(pid, logfile, start_epoch):
    with open(PID_FILE, "w") as f:
        json.dump({"pid": pid, "logfile": logfile, "start_epoch": start_epoch}, f)


def load_record_state():
    try:
        with open(PID_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def clear_record_state():
    try:
        os.unlink(PID_FILE)
    except FileNotFoundError:
        pass
    except PermissionError:
        # state file owned by root (sudo daemon); try to remove as root
        subprocess.run(["sudo", "rm", "-f", PID_FILE], check=False)


def running_record_daemons():
    """Return PIDs of running `uart.py record` daemons (excludes self and `--stop` invocations)."""
    me = os.getpid()
    try:
        out = subprocess.check_output(
            ["pgrep", "-af", "uart.py.*record"], text=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    pids = []
    for line in out.strip().splitlines():
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmdline = parts[1]
        if pid == me or "--stop" in cmdline or "--status" in cmdline:
            continue
        pids.append(pid)
    return pids


def kill_pids(pids, label="record daemon"):
    """SIGTERM then SIGKILL (if needed) a list of PIDs. Returns count killed."""
    killed = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except ProcessLookupError:
            continue
        except PermissionError:
            # daemon started with sudo; need sudo to kill
            subprocess.run(["sudo", "kill", "-TERM", str(pid)], check=False)
            killed += 1
    # grace period
    for _ in range(20):  # up to 2s total
        alive = []
        for pid in pids:
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except (ProcessLookupError, PermissionError):
                pass
        if not alive:
            break
        time.sleep(0.1)
    # SIGKILL stragglers
    for pid in pids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            subprocess.run(["sudo", "kill", "-KILL", str(pid)], check=False)
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return killed


# ── Sub-command: recv ─────────────────────────────────────────────

def cmd_recv(args):
    """Receive and display serial output."""
    device = resolve_device(args.device)
    timeout = args.timeout

    print(f"[RECV] {device} @ {args.baud} baud, timeout={timeout}s")
    print(f"[RECV] Press Ctrl+C to stop\n")

    try:
        ser = serial.Serial(device, args.baud, timeout=1)
    except serial.SerialException as e:
        print(f"[ERROR] Cannot open {device}: {e}", file=sys.stderr)
        if "Permission" in str(e):
            print("[HINT] Try: sudo python3 uart.py recv", file=sys.stderr)
            print("[HINT] Or:  sudo usermod -aG dialout $USER", file=sys.stderr)
        sys.exit(1)

    start = time.time()
    try:
        while True:
            if timeout and (time.time() - start) >= timeout:
                break
            line = ser.readline()
            if line:
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                print(f"[{ts}] {text}")
    except KeyboardInterrupt:
        print("\n[RECV] Stopped.")
    finally:
        ser.close()


# ── Sub-command: send ─────────────────────────────────────────────

def cmd_send(args):
    """Send a command to the serial port and capture response.

    If a `record` daemon is currently running, we cooperate: write the command
    bytes via a raw fd (kernel tty allows concurrent writers) and tail the
    daemon's log file for the capture window — this avoids "device busy" and
    lets the user run send+record concurrently.
    """
    device = resolve_device(args.device)
    command = args.command
    capture_time = args.capture

    if not command:
        print("[ERROR] No command specified. Usage: uart.py send \"ls\"", file=sys.stderr)
        sys.exit(1)

    # Cooperative mode: a record daemon owns the port
    daemons = running_record_daemons()
    state = load_record_state()
    if daemons and state:
        logfile = state["logfile"]
        print(f"[SEND] record daemon PID {daemons[0]} is live; cooperative send via log tail")
        print(f"[SEND] Command: {command}")
        print(f"[SEND] Log: {logfile}")
        print(f"[SEND] Capturing response for {capture_time}s...\n")

        # Note the log file size before sending — that's our tail start.
        try:
            start_offset = os.path.getsize(logfile)
        except OSError:
            start_offset = 0

        # Write via raw fd — kernel tty layer allows this even while another
        # process is reading. Termios settings set up by the daemon persist.
        try:
            fd = os.open(device, os.O_WRONLY | os.O_NOCTTY)
        except PermissionError:
            print(f"[ERROR] Cannot open {device} write-only (try sudo).", file=sys.stderr)
            sys.exit(1)
        try:
            os.write(fd, (command + "\r\n").encode("utf-8"))
        finally:
            os.close(fd)

        # Tail the log file for capture_time seconds
        end = time.time() + capture_time
        cur = start_offset
        while time.time() < end:
            time.sleep(0.15)
            try:
                size = os.path.getsize(logfile)
            except OSError:
                continue
            if size > cur:
                try:
                    with open(logfile, "r", errors="replace") as f:
                        f.seek(cur)
                        chunk = f.read(size - cur)
                except PermissionError:
                    # log owned by root; fall back to sudo cat
                    chunk = subprocess.run(
                        ["sudo", "dd", f"if={logfile}", f"skip={cur}", "iflag=skip_bytes",
                         "status=none"], capture_output=True, text=True,
                    ).stdout
                sys.stdout.write(chunk)
                sys.stdout.flush()
                cur = size
        return

    # Exclusive mode (no daemon running)
    print(f"[SEND] {device} @ {args.baud} baud")
    print(f"[SEND] Command: {command}")
    print(f"[SEND] Capturing response for {capture_time}s...\n")

    try:
        ser = serial.Serial(device, args.baud, timeout=1)
    except serial.SerialException as e:
        print(f"[ERROR] Cannot open {device}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        ser.write((command + "\r\n").encode("utf-8"))
        ser.flush()
        start = time.time()
        while (time.time() - start) < capture_time:
            line = ser.readline()
            if line:
                ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                print(f"[{ts}] {text}")
    except KeyboardInterrupt:
        print("\n[SEND] Stopped.")
    finally:
        ser.close()


# ── Sub-command: record ───────────────────────────────────────────

def cmd_record(args):
    """Record serial log to a timestamped file."""

    # ── Status (diagnostic) ──
    if args.status:
        daemons = running_record_daemons()
        state = load_record_state()
        if not daemons and not state:
            print("[RECORD] No active recording.")
            return
        if daemons:
            print(f"[RECORD] Running daemons: {daemons}")
        if state:
            tracked = state["pid"]
            print(f"[RECORD] State file tracks PID {tracked} → {state['logfile']}")
            if tracked not in daemons:
                print("[RECORD] ⚠ tracked PID is not running (stale state)")
        orphans = [p for p in daemons if not state or p != state["pid"]]
        if orphans:
            print(f"[RECORD] ⚠ orphan daemons detected: {orphans} "
                  "(use `record --stop` to clean them up)")
        return

    # ── Stop recording ──
    if args.stop:
        daemons = running_record_daemons()
        state = load_record_state()

        if not daemons and not state:
            print("[RECORD] No active recording found.")
            return

        # Union: state PID + all running daemons (handles orphans)
        all_pids = set(daemons)
        if state:
            all_pids.add(state["pid"])

        killed = kill_pids(list(all_pids))
        print(f"[RECORD] Stopped {killed} daemon(s): {sorted(all_pids)}")

        # Write end marker to tracked log (if we have it)
        if state:
            logfile = state["logfile"]
            start_epoch = state["start_epoch"]
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
            except PermissionError:
                # log owned by root; append via sudo tee
                subprocess.run(
                    ["sudo", "tee", "-a", logfile],
                    input=end_marker, text=True,
                    stdout=subprocess.DEVNULL, check=False,
                )
                print(f"[RECORD] End marker written to {logfile} (via sudo)")
            except Exception as e:
                print(f"[ERROR] Cannot write end marker: {e}", file=sys.stderr)

        clear_record_state()
        return

    # ── Start recording ──

    # Single-instance enforcement: any existing daemon (tracked or orphan)
    # must be stopped before we fork a new one, otherwise multiple readers
    # compete for /dev/ttyUSB* and output gets garbled/split.
    daemons = running_record_daemons()
    state = load_record_state()
    stale_pids = [p for p in daemons]
    if state and state["pid"] not in stale_pids:
        # state might point to a dead pid; include only if alive
        try:
            os.kill(state["pid"], 0)
            stale_pids.append(state["pid"])
        except ProcessLookupError:
            pass

    if stale_pids:
        print(f"[RECORD] Existing daemon(s) found: {stale_pids} — stopping them first")
        kill_pids(stale_pids)
        clear_record_state()

    device = resolve_device(args.device)
    platform = args.platform or detect_platform()
    log_dir = args.output or os.environ.get("UART_LOG_DIR") or "./uart_logs"

    os.makedirs(log_dir, exist_ok=True)

    # Filename: <platform>-<YYYY_MMDD_HHMM>.log
    now = datetime.now()
    timestamp = now.strftime("%Y_%m%d_%H%M")
    logfile = os.path.join(log_dir, f"{platform}-{timestamp}.log")

    print(f"[RECORD] Device:   {device} @ {args.baud} baud")
    print(f"[RECORD] Platform: {platform}")
    print(f"[RECORD] Log file: {logfile}")

    # Fork a child process to do the recording
    pid = os.fork()

    if pid == 0:
        # ── Child process: record loop ──
        # Detach from terminal session
        os.setsid()

        # Redirect std fds to /dev/null so the daemon isn't killed by SIGPIPE
        # when the parent's pipe (e.g. `| tail` in the invoker) closes. This
        # was the real reason daemons died seconds after reboot captures:
        # nothing to do with serial errors, just an inherited-pipe footgun.
        try:
            devnull = os.open("/dev/null", os.O_RDWR)
            for fd in (0, 1, 2):
                os.dup2(devnull, fd)
            os.close(devnull)
        except OSError:
            pass
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        try:
            ser = serial.Serial(device, args.baud, timeout=1)
        except serial.SerialException as e:
            sys.stderr.write(f"[ERROR] Cannot open {device}: {e}\n")
            os._exit(1)

        start_marker = (
            f"----------- log-start @ {now.strftime('%Y-%m-%d %H:%M:%S')} -----------\n"
        )

        def _shutdown(signum, frame):
            ser.close()
            os._exit(0)

        signal.signal(signal.SIGTERM, _shutdown)

        # Record loop survives transient serial errors (framing glitches,
        # USB re-enumeration) so we keep capturing across target reboots.
        # Rotates to a fresh log file when the current segment exceeds
        # MAX_LOG_SIZE — xHCI spam can fill GB/hour on some boards.
        # Only SIGTERM (via `record --stop`) actually shuts us down.
        consecutive_reopen_failures = 0
        MAX_REOPEN_FAILURES = 30   # ~30s of total unavailability → give up

        # Parse logfile into (base, ext) once so rotation segments share the stem.
        _log_base, _log_ext = os.path.splitext(logfile)
        current_logfile = logfile
        segment = 1
        write_counter = 0  # check size every N writes to cap fstat cost

        f = open(current_logfile, "w")
        f.write(start_marker)
        f.flush()

        try:
            while True:
                # Rotation check (every 64 writes — fstat is cheap but not free)
                if write_counter >= 64:
                    write_counter = 0
                    try:
                        if os.fstat(f.fileno()).st_size >= MAX_LOG_SIZE:
                            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            f.write(f"----------- rotate-out @ {ts} | next: segment {segment+1} -----------\n")
                            f.flush()
                            f.close()
                            segment += 1
                            current_logfile = f"{_log_base}.part{segment:02d}{_log_ext}"
                            f = open(current_logfile, "w")
                            f.write(f"----------- rotate-in @ {ts} | segment {segment} -----------\n")
                            f.flush()
                            # Update state file so `record --stop` writes end marker to the current segment
                            state = load_record_state()
                            if state:
                                save_record_state(state["pid"], os.path.abspath(current_logfile), state["start_epoch"])
                    except OSError:
                        pass

                try:
                    line = ser.readline()
                except (serial.SerialException, OSError) as e:
                    # Common during target reboot: framing errors as the
                    # target's UART TX pin resets. Note it and try to reopen.
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    f.write(f"[{ts}] [uart.py] serial error: {e}; reopening...\n")
                    f.flush()
                    try:
                        ser.close()
                    except Exception:
                        pass
                    try:
                        ser = serial.Serial(device, args.baud, timeout=1)
                        consecutive_reopen_failures = 0
                        f.write(f"[{ts}] [uart.py] reopened {device}\n")
                        f.flush()
                    except (serial.SerialException, OSError) as e2:
                        consecutive_reopen_failures += 1
                        f.write(f"[{ts}] [uart.py] reopen failed ({consecutive_reopen_failures}/{MAX_REOPEN_FAILURES}): {e2}\n")
                        f.flush()
                        if consecutive_reopen_failures >= MAX_REOPEN_FAILURES:
                            f.write(f"[{ts}] [uart.py] giving up after {MAX_REOPEN_FAILURES} reopen attempts\n")
                            f.flush()
                            os._exit(1)
                        time.sleep(1)
                    continue

                if line:
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    f.write(f"[{ts}] {text}\n")
                    f.flush()
                    write_counter += 1
        finally:
            try:
                f.close()
            except Exception:
                pass
    else:
        # ── Parent process ──
        save_record_state(pid, os.path.abspath(logfile), time.time())
        print(f"[RECORD] Recording started (PID: {pid})")

        # Decide mode based on flags:
        # - No triggers and no --cmd → background daemon (return, user stops later)
        # - Any trigger OR --cmd → foreground orchestration (send cmds, wait,
        #   auto-stop, exit)
        triggers_given = bool(args.duration or args.size_bytes or args.expect
                              or args.timeout or args.cmd)
        if not triggers_given:
            print(f"[RECORD] Stop with: python3 {__file__} record --stop")
            return

        # Wait briefly for daemon to open the log file
        for _ in range(20):
            if os.path.exists(os.path.abspath(logfile)):
                break
            time.sleep(0.1)
        _orchestrate_record_foreground(args, os.path.abspath(logfile))


# ── Foreground orchestration: cooperative send + conditional wait ────

def _orchestrate_record_foreground(args, logfile):
    """Runs after the daemon is forked. Sends --cmd(s), then waits until any
    of --duration / --size / --expect / --timeout triggers, tails the log for
    display (if --tail), then stops the daemon and prints a summary.
    """
    device = args.device
    pattern = re.compile(args.expect) if args.expect else None

    # Daemon warm-up: after fork, the child opens /dev/ttyUSB*, enters its
    # readline loop, and starts writing captured data. Before that happens,
    # any bytes arriving on RX (e.g. the kernel-layer ECHO of our --cmd
    # characters) would be lost — pyserial hasn't attached yet. Wait for
    # BOTH (a) the daemon state file AND (b) the first line past log-start
    # to appear. If nothing comes in 500 ms (e.g. truly idle console), we
    # fall through — the `reboot` echo only matters when there IS a shell.
    warm_deadline = time.time() + 1.5
    while time.time() < warm_deadline:
        try:
            sz = os.path.getsize(logfile)
        except OSError:
            sz = 0
        # start_marker is ~54 bytes; require at least one captured line past it
        if sz > 80:
            break
        time.sleep(0.05)

    # Snapshot the pre-cmd file size so our window starts here — echo of
    # our commands will appear AFTER this offset.
    try:
        pre_offset = os.path.getsize(logfile)
    except OSError:
        pre_offset = 0
    cur = pre_offset

    # Send commands via the raw-fd cooperative path (kernel tty allows
    # concurrent writers; daemon keeps owning the reader side).
    if args.cmd:
        try:
            fd = os.open(device, os.O_WRONLY | os.O_NOCTTY)
        except OSError as e:
            print(f"[RECORD] ERROR: cannot open {device} for write: {e}", file=sys.stderr)
            _trigger_stop(args)
            sys.exit(2)
        try:
            for c in args.cmd:
                print(f"[RECORD] → {c}")
                os.write(fd, (c + "\r\n").encode("utf-8"))
                time.sleep(0.2)  # gap between commands lets each echo arrive
        finally:
            os.close(fd)

    # Choose the deadline. --timeout overrides --duration; else --duration.
    # If neither, None → no time-based stop.
    max_wall = args.timeout if args.timeout else args.duration
    start = time.time()
    matched = None

    if args.tail:
        print(f"[RECORD] tailing log (Ctrl-C to stop early)...")

    try:
        while True:
            # Time termination
            if max_wall and (time.time() - start) >= max_wall:
                break
            time.sleep(0.2)
            # Follow rotation if daemon rolled the segment
            st = load_record_state()
            if st and st.get("logfile") and st["logfile"] != logfile:
                logfile = st["logfile"]
                cur = 0
            try:
                size = os.path.getsize(logfile)
            except OSError:
                continue
            # Size termination
            if args.size_bytes and (size - pre_offset) >= args.size_bytes:
                break
            if size <= cur:
                continue
            # Read new chunk
            try:
                with open(logfile, "r", errors="replace") as lf:
                    lf.seek(cur)
                    chunk = lf.read(size - cur)
            except PermissionError:
                chunk = subprocess.run(
                    ["sudo", "dd", f"if={logfile}", f"skip={cur}",
                     "iflag=skip_bytes", "status=none"],
                    capture_output=True, text=True,
                ).stdout
            cur = size
            if args.tail and chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
            if pattern:
                m = pattern.search(chunk)
                if m:
                    matched = m.group(0)
                    break
    except KeyboardInterrupt:
        print("\n[RECORD] interrupted by user")

    elapsed = time.time() - start
    try:
        total_bytes = os.path.getsize(logfile) - pre_offset
    except OSError:
        total_bytes = 0

    # Auto-stop the daemon and flush end marker
    _trigger_stop(args)

    # Summary
    print(f"\n[RECORD] captured {total_bytes/1024:.1f} KB in {elapsed:.1f}s from {logfile}")
    if pattern:
        if matched:
            print(f"[RECORD] matched '{matched.strip()}'")
        else:
            print(f"[RECORD] pattern /{args.expect}/ NOT matched before stop", file=sys.stderr)

    # Exit code: 0 on success, 1 if --expect required and not matched
    sys.exit(0 if (not pattern or matched) else 1)


def _trigger_stop(args):
    """Internally trigger `record --stop` logic without re-exec."""
    import argparse as _ap
    stop_args = _ap.Namespace(
        device=args.device, baud=args.baud,
        stop=True, status=False, platform=args.platform, output=args.output,
        # Orchestrate fields present so cmd_record sees a complete namespace
        duration=None, size_bytes=None, expect=None, timeout=None,
        cmd=None, tail=False,
    )
    cmd_record(stop_args)


# ── Size parsing helper ───────────────────────────────────────────

def _parse_size(s):
    """Accept '10M', '10MB', '500K', '1024' → bytes. Returns None on None input."""
    if s is None:
        return None
    s = str(s).strip().upper()
    if not s:
        return None
    units = [("GB", 1 << 30), ("G", 1 << 30), ("MB", 1 << 20),
             ("M", 1 << 20), ("KB", 1 << 10), ("K", 1 << 10), ("B", 1)]
    for suf, mult in units:
        if s.endswith(suf):
            return int(float(s[:-len(suf)]) * mult)
    return int(s)


def _parse_duration(s):
    """systemd-style duration → seconds (float). Returns None on None input.

    Accepts plain number (seconds) or unit-suffixed forms, possibly combined:
        30        → 30.0
        30s       → 30.0
        2m        → 120.0
        1h        → 3600.0
        1h30m     → 5400.0
        2.5m      → 150.0
    """
    if s is None:
        return None
    s = str(s).strip().lower()
    if not s:
        return None
    # Bare number → seconds
    try:
        return float(s)
    except ValueError:
        pass
    units = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
    total = 0.0
    buf = ""
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isdigit() or ch == ".":
            buf += ch
            i += 1
            continue
        # unit: prefer 2-char 'ms' before 1-char 's'
        unit = s[i:i + 2] if s[i:i + 2] in units else s[i]
        if unit not in units:
            raise ValueError(f"bad duration {s!r}: unknown unit near {unit!r}")
        if not buf:
            raise ValueError(f"bad duration {s!r}: missing number before {unit!r}")
        total += float(buf) * units[unit]
        buf = ""
        i += len(unit)
    if buf:
        # trailing bare number → seconds
        total += float(buf)
    return total


# ── CLI entry point ───────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="UART serial communication tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--device", "-d", help="Serial device path (auto-detect if omitted)")
    parser.add_argument("--baud", "-b", type=int, default=None, help="Baud rate (auto-detect if omitted)")

    sub = parser.add_subparsers(dest="subcmd", help="Sub-command")

    # recv
    p_recv = sub.add_parser("recv", help="Receive and display serial output")
    p_recv.add_argument("timeout", nargs="?", type=float, default=None,
                        help="Receive timeout in seconds (default: unlimited, Ctrl+C to stop)")

    # send
    p_send = sub.add_parser("send", help="Send a command to the serial port")
    p_send.add_argument("command", help="Command string to send")
    p_send.add_argument("capture", nargs="?", type=float, default=3,
                        help="Response capture time in seconds (default: 3)")

    # record (unified: daemon management + foreground capture presets)
    p_record = sub.add_parser(
        "record",
        help="Record serial log. No triggers = background daemon. With "
             "--duration/--size/--expect or --cmd = foreground run that auto-stops.",
    )
    # Daemon control
    p_record.add_argument("--stop", action="store_true",
                          help="Stop active recording (kills all orphan daemons too)")
    p_record.add_argument("--status", action="store_true",
                          help="Show running daemons / orphan detection")
    # Output
    p_record.add_argument("--platform", "-p", help="Platform name (auto-detect if omitted)")
    p_record.add_argument("--output", "-o",
                          help="Output directory (default: $UART_LOG_DIR or ./uart_logs)")
    # Foreground trigger set — presence of ANY switches record to fg mode
    p_record.add_argument("--cmd", "-c", action="append", default=None, metavar="CMD",
                          help="Command to send to the target after record starts. "
                               "Repeat to send multiple. Uses cooperative raw-fd write so "
                               "it coexists with the daemon reading the port.")
    p_record.add_argument("--duration", "-t", dest="duration_raw", default=None, metavar="DUR",
                          help="Stop after this duration. Accepts plain seconds (30) or suffix "
                               "form (30s, 2m, 1h, 1h30m). systemd-style.")
    p_record.add_argument("--size", "-s", dest="size_raw", default=None, metavar="SIZE",
                          help="Stop after capturing this much data. Accepts K/M/G suffix "
                               "(e.g. 10M, 512K, 1G)")
    p_record.add_argument("--expect", "-e", default=None, metavar="REGEX",
                          help="Stop when a log line matches this regex "
                               "(exit 0 on match, 1 on timeout)")
    p_record.add_argument("--timeout", dest="timeout_raw", default=None, metavar="DUR",
                          help="Upper safety bound for --expect (same syntax as --duration). "
                               "Overrides --duration if both set.")
    p_record.add_argument("--tail", action="store_true",
                          help="Stream new log content to stdout while waiting")

    args = parser.parse_args()

    if args.subcmd is None:
        parser.print_help()
        sys.exit(0)

    # Auto-detect baud rate if not specified (skip for record --stop/--status)
    skip_baud = (args.subcmd == "record"
                 and (getattr(args, "stop", False) or getattr(args, "status", False)))
    if args.baud is None and not skip_baud:
        device = resolve_device(args.device)
        args.baud = detect_baud(device)
        args.device = device

    # Normalize record's duration/timeout/size string args → canonical fields
    if args.subcmd == "record":
        args.size_bytes = _parse_size(getattr(args, "size_raw", None))
        args.duration = _parse_duration(getattr(args, "duration_raw", None))
        args.timeout = _parse_duration(getattr(args, "timeout_raw", None))

    {
        "recv": cmd_recv,
        "send": cmd_send,
        "record": cmd_record,
    }[args.subcmd](args)


if __name__ == "__main__":
    main()
