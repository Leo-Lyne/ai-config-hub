---
name: uart-debug
description: Generic UART / serial-port debugging infrastructure for any embedded target (MCU, Linux BSP, router, printer, etc.). Three sub-commands — `recv` (timestamped read), `send` (write + short capture), `record` (long-running capture with optional command injection and time/size/pattern terminators). Use when the user says /uart-debug, or asks to read/send/record serial port data, capture UART logs, or monitor serial output. Works with any USB-to-UART adapter (CH340 / CP2102 / FTDI / built-in UART) on WSL2 or native Linux. This skill is domain-neutral — it does not prescribe scenario recipes; domain skills (Android BSP, MCU flash/debug, router diag, etc.) compose these primitives to solve their own problems.
---

# UART / Serial Port — Generic Debug Infrastructure

A minimal, domain-neutral serial I/O skill. Three sub-commands + a small set of orthogonal flags. Combine them however your workflow requires.

```
SCRIPT="$HOME/.claude/skills/uart-debug/scripts/uart.py"
```

## First-time setup

Bundle pyserial into `deps/.venv/` (so the skill is self-contained):

```bash
bash $HOME/.claude/skills/uart-debug/deps/fetch_deps.sh   # online: fetch wheels
bash $HOME/.claude/skills/uart-debug/deps/install.sh      # build deps/.venv
```

`uart.py` auto-detects: tries ambient `import serial` first, falls back to
`deps/.venv/bin/python` via `os.execv` if pyserial isn't available system-wide.
You can keep invoking with plain `python3 $SCRIPT …` — the script re-execs
transparently.

## Sub-commands

| Sub-command | Purpose |
|---|---|
| `recv [SEC]` | Passive timestamped read, print to stdout |
| `send "CMD" [SEC]` | Write a line, capture response for `SEC` seconds, print to stdout |
| `record [flags]` | Persist a timestamped log to file; optionally inject commands and/or auto-stop on duration / size / pattern |
| `record --stop` | Stop a running recording (sweeps orphan daemons too) |
| `record --status` | Show running recording state |

`recv` / `send` are one-shot helpers. `record` is the composition engine.

## `record` flags

| Flag | Meaning |
|---|---|
| `-o DIR` | Output directory (default `$UART_LOG_DIR` or `./uart_logs`). Filename auto-generated: `<platform>-<YYYY_MMDD_HHMM>.log`. Rotates to `.partNN.log` at 100 MB (override via `UART_MAX_LOG_BYTES`). |
| `-p NAME` | Platform name for filename (defaults to a cwd-derived tag). |
| `--cmd "..." / -c` | Send a line to the target at the start of recording. Repeatable; sent in order with a small inter-command gap. Uses a cooperative raw-fd writer so it coexists with the daemon reading the port. |
| `--duration DUR / -t` | Stop after this wall-clock window. systemd syntax: `30s`, `2m`, `1h`, `1h30m`, or plain seconds. |
| `--size SIZE / -s` | Stop after N bytes captured. `K/M/G` suffix. |
| `--expect REGEX / -e` | Stop when a log line matches. Pair with `--timeout` as a safety bound. |
| `--timeout DUR` | Upper bound for `--expect` (same syntax as `--duration`). |
| `--tail` | Stream the log to stdout while waiting. |
| No terminator | Background daemon mode — detaches, survives parent exit. Stop with `record --stop`. |

If more than one terminator is given, the first to fire wins.

## Picking a terminator — guidance

Choose based on what you actually know up front:

- **`--duration` — the right default.** You almost always have a reasonable estimate of "how long this thing takes". A generous time window captures the event *and its aftermath*, then `grep` trims offline.
- **`--size`** — when you care about disk use rather than time (long-running soaks, noisy consoles).
- **`--expect`** — only for "wait for a rare event I can't time-box". Panics, coredumps, assert-prints, deadlock markers. Do **not** use it to "stop early once the interesting part appears" — you will truncate the log at exactly the wrong place, and picking the regex hardcodes a scenario assumption into a general-purpose tool.
- **Background daemon** — attended interactive sessions where you want recording running in parallel with manual poking.

**Rule of thumb: capture generously, filter offline.** Log space is cheap; missed data is not.

```
Do you know when to stop?
├── Roughly, in time units       → --duration 30s / 2m / 5m
├── Capping disk use             → --size 100M
├── Waiting for a rare event     → --expect "REGEX" --timeout SAFETY
└── Open-ended / attended        → no terminator (bg daemon)

Do you need to poke the target?
├── A shell command on the console → --cmd "CMD" (repeatable)
├── External action                → run it yourself; record just observes
└── Pure observation               → omit --cmd
```

## Minimal examples (not scenario-specific)

```bash
# Just watch for 5 seconds
sudo python3 $SCRIPT recv 5

# Send a line, capture 3 s of response
sudo python3 $SCRIPT send "some-command"

# Capture 2 minutes around a triggered action
sudo python3 $SCRIPT record --duration 2m -o ./uart_logs

# Capture until 50 MB, then stop
sudo python3 $SCRIPT record --size 50M -o ./uart_logs

# Send a command at t=0, capture 90 s of the aftermath
sudo python3 $SCRIPT record --cmd "SOME_CMD" --duration 90s -o ./uart_logs

# Wait up to 10 min for a specific line to appear
sudo python3 $SCRIPT record --expect "PANIC|Oops" --timeout 10m -o ./uart_logs

# Open-ended background recording; stop manually later
sudo python3 $SCRIPT record -o ./uart_logs
# ...
sudo python3 $SCRIPT record --stop
```

These examples are illustrative — domain-specific workflows (Android boot log, MCU firmware trace, router crash dump, etc.) belong in their own skills that compose these primitives.

## Background daemon behaviour

- Forked with `os.setsid()`; stdio → `/dev/null` (so `SIGPIPE` from a closing parent pipe doesn't kill it).
- Closes and reopens the port on `SerialException` / framing errors — survives target resets.
- Rotates `.partNN.log` at 100 MB.
- State tracked at `/tmp/.uart_record.json`.
- `record --stop` kills the tracked PID **and** any orphan `uart.py record` daemons found via `pgrep`.

## Send during an active recording

`send` detects an active `record` daemon and switches to **cooperative mode**: it writes via a raw fd (`O_WRONLY | O_NOCTTY`) and then tails the daemon's log for the response window. The daemon keeps owning the reader side — no port contention, no lost bytes.

## Global options

| Option | Purpose |
|---|---|
| `-d /dev/ttyUSB1` | Force a specific device; skips auto-detect |
| `-b 115200` | Force a specific baud; skips auto-detect |

Device + baud are auto-detected on first use and cached at `/tmp/.uart_cache.json` — subsequent calls skip the probe.

## WSL2 note

If `recv` / `send` reports `No serial device found`, the USB-serial adapter is on the Windows side and hasn't been attached to WSL. Use the `usbip` skill:

```
/usbip list
/usbip attach serial
```

## Error table

| Symptom | Meaning | Fix |
|---|---|---|
| `No serial device found` | `/dev/ttyUSB*` missing | `/usbip attach serial` on WSL2, or check cable/driver |
| `Permission denied` | Not in `dialout`, not root | `sudo`, or `sudo usermod -aG dialout $USER` + re-login |
| `Device busy` | Another process owns the port | `sudo fuser /dev/ttyUSB0`; `record --stop` sweeps known daemons |
| Garbled output | Wrong baud | `-b 1500000` (some Rockchip), `-b 115200`, etc. |
| `record --status` shows orphan | Previous record was cancelled mid-run | `record --stop` |
| Harness reports `Exit code 144` but log looks fine | Cosmetic artifact (daemon survived parent's pipe closing) | Verify via `record --status` and the log content |

## Design principle

This skill is a **thin, domain-neutral wrapper around pyserial**. It deliberately exposes orthogonal primitives (`recv` / `send` / `record` × flags) rather than named scenarios. Scenario recipes (Android BSP boot capture, MCU flash+probe, router crash-dump wait, etc.) belong in **separate domain skills** that shell out to this one — so this skill stays small, portable, and useful to developers outside any single domain.

Alternative tools in this space: `tio`, `picocom`, `minicom`, `pyserial-miniterm`, `screen`. `uart.py` adds a persistent background-daemon model, cooperative send-during-record, and systemd-style duration/size terminators on top of the same basic idea.
