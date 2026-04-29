---
name: wsl-host-control
description: Drive the Windows host from inside WSL — list every installed app (UWP + Win32) and launch any of them by name, open URLs/files in their default app, dodge the Session-0 single-instance trap that hides Chrome windows, read/write the Windows clipboard, send toast notifications, list/kill Windows processes (with SessionId so you can spot the trap), translate paths, and run arbitrary PowerShell elevated through a single UAC prompt to control system settings (services, registry HKLM, scheduled tasks, anything PowerShell can do as admin). Use this skill WHENEVER the user is in WSL and asks to do anything on the Windows side: "open Chrome / Edge / Spotify / VSCode on Windows", "show me what apps are installed", "launch the Settings app / display settings / network settings", "open this URL / PDF / folder on the host", "copy this to the Windows clipboard", "what's on my clipboard", "kill that Windows process", "stop / start / restart a Windows service", "set a registry key", "send a Windows toast notification", "为什么 WSL 启动的 Chrome 看不到窗口", "在 Windows 上打开 X", "用管理员权限改 Windows 系统设置", "/wsl-host-control 任意子命令". ALSO use when interop seemingly succeeds (`exit 0`) but no GUI window appears — that is almost always the Session-0 trap, and `check` / `chrome` here are the right diagnostics.
---

# wsl-host-control — drive Windows from WSL

Subcommand-style tool. Wraps Windows interop so you don't have to remember the quoting tricks, the Session-0 trap, the `--user-data-dir` Chrome workaround, or how to capture output from an elevated PowerShell.

```
TOOL="$HOME/.claude/skills/wsl-host-control/scripts/wsl_host.py"
```

## Sub-commands

| User says | Run |
|---|---|
| `/wsl-host-control apps` | `python3 $TOOL apps` |
| `/wsl-host-control apps --search chrome` | `python3 $TOOL apps --search chrome` |
| `/wsl-host-control open <name>` | `python3 $TOOL open "<name>"` |
| `/wsl-host-control chrome [url]` | `python3 $TOOL chrome [url]` |
| `/wsl-host-control url <url-or-file>` | `python3 $TOOL url "<target>"` |
| `/wsl-host-control settings <page>` | `python3 $TOOL settings display` |
| `/wsl-host-control clip-copy` | `echo hi \| python3 $TOOL clip-copy` |
| `/wsl-host-control clip-paste` | `python3 $TOOL clip-paste` |
| `/wsl-host-control notify <title> <body>` | `python3 $TOOL notify "Build" "Done"` |
| `/wsl-host-control ps [filter]` | `python3 $TOOL ps chrome` |
| `/wsl-host-control kill <pid\|name>` | `python3 $TOOL kill 1234` (add `--admin` if needed) |
| `/wsl-host-control session` | `python3 $TOOL session` |
| `/wsl-host-control check` | `python3 $TOOL check` |
| `/wsl-host-control path-w <linux-path>` | `python3 $TOOL path-w /home/leo/x.md` |
| `/wsl-host-control path-u <win-path>` | `python3 $TOOL path-u 'C:\Users\Leo'` |
| `/wsl-host-control admin-run "<powershell>"` | one-shot elevated PowerShell, output captured |
| `/wsl-host-control svc <name> [start\|stop\|restart\|status]` | service mgmt (auto-elevates) |
| `/wsl-host-control reg get\|set\|del <key> [--value V] [--data D]` | registry (HKLM auto-elevates) |

## How app discovery works

`apps` runs PowerShell `Get-StartApps`, which is what populates the Start Menu — **everything that has a Start Menu entry, both UWP/Store apps and Win32 shortcuts.** Result is cached in `~/.cache/wsl-host-control/apps.json` for 24h; pass `--refresh` to force re-enumeration after installing/removing software.

`open <name>` does substring match (case-insensitive) against the cached list:

- Single match → launches.
- Multiple matches → prints them and exits 1, unless `--first` is passed or the query is an exact name match.
- Zero matches → suggests `apps refresh`.

UWP apps have an AppUserModelID like `Microsoft.WindowsCalculator_8wekyb3d8bbwe!App`; the skill launches those via `explorer.exe shell:AppsFolder\<id>`. Win32 shortcuts (paths ending in `.lnk` or absolute exe paths) launch via PowerShell `Start-Process`. UWP apps usually ignore `args`; Win32 apps accept them.

## The Chrome / Session-0 trap

This is the gotcha that motivated the skill. Symptom: `chrome.exe https://...` exits 0, no window anywhere.

What's happening: a service-mode launcher (e.g. an OpenClaw agent) on this host already runs Chrome inside **Session 0** (Windows's hidden services session). Chrome's single-instance IPC then forwards every subsequent launch's URL to *that* hidden Chrome — your visible desktop never sees a window.

The `chrome` subcommand sidesteps it by passing `--user-data-dir=%LOCALAPPDATA%\ChromeTemp-WslHostControl --new-window`, which forces a brand-new Chrome process with its own profile that doesn't talk to the Session-0 instance.

Diagnose with `session` (shows your interactive console session id) and `ps chrome` (the `SessionId` column tells you where each Chrome lives — `0` = trapped, `>0` = your desktop). The `check` subcommand runs an automated smoke test using Notepad.

## Admin / UAC model

Privileged operations (`svc start/stop`, `reg set/del` on HKLM, `kill --admin`, `admin-run`) wrap their PowerShell in:

```powershell
Start-Process powershell -Verb RunAs -Wait -ArgumentList ...
```

This triggers **one UAC prompt per call**. There's no persistent admin session — each privileged subcommand prompts again. If you're going to run several admin operations, batch them: `admin-run "Stop-Service A; Stop-Service B; Set-ItemProperty ..."` — that's one prompt for the whole sequence.

Output from elevated PowerShell is captured by writing all streams to a Windows temp file (because `-Verb RunAs` cannot redirect stdio directly) and reading it back. Errors appear in the captured output, not in stderr.

If the user is on Remote Desktop and UAC is set to "secure desktop", the prompt may not appear in their session — they'll need to either change UAC level or run the command from an already-elevated PowerShell on the host.

## Common settings shortcuts

`settings <page>` prepends `ms-settings:` if missing. Useful pages:

| Page | Opens |
|---|---|
| `display` | Display settings (resolution, scaling) |
| `network` | Network status |
| `bluetooth` | Bluetooth devices |
| `sound` | Sound devices |
| `windowsupdate` | Windows Update |
| `defaultapps` | Default apps for file types / protocols |
| `about` | System info |
| `privacy` | Privacy main page |
| `apps-features` | Installed apps |

For older Control Panel applets, use `admin-run "control.exe /name Microsoft.DeviceManager"` (or whichever applet); the skill doesn't shortcut these because the Settings app covers most modern needs.

## Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `open <name>` says "no installed app matches" | Cache stale, or app installed under a non-Start-Menu name | `apps --refresh`; if still missing, the app has no Start Menu entry — launch it by full path via `admin-run` or directly |
| `chrome` works but `open chrome` opens the trapped Chrome | `open` calls `Start-Process`, which still hits Chrome's single-instance | Use `chrome` subcommand for Chrome specifically |
| `clip-paste` output has carriage returns | PowerShell emits CRLF | The skill already strips `\r`; if you bypass it, pipe through `sed 's/\r$//'` |
| `clip-copy` / `clip-paste` returns "Access is denied" or "Requested Clipboard operation did not succeed" | Active console session is locked or disconnected (RDP closed without sign-out, screen-locked, or some clipboard hooking software is interfering) | Unlock / reconnect the desktop session; failing that, the clipboard isn't reachable from this WSL — fall back to a temp file via `path-w` and have the user paste manually |
| `admin-run` prompts UAC but produces no output | Script raised an exception inside the elevated session | Check the temp file contents — exceptions are captured to it; the wrapper catches and writes them, so re-run with simpler PowerShell to isolate |
| UAC prompt never appears | Headless / RDP / locked desktop, or UAC=Never | Run from an already-elevated PowerShell, or change UAC level temporarily |
| `apps` returns nothing | First run on a host with PowerShell-execution-policy locked down | `admin-run "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"` once |
| `cmd.exe /c start ...` errors with "Access is denied" (when bypassing this skill) | WSL CWD is `\\wsl.localhost\...`, cmd dropped to System32 | Use this skill's `url`/`open` instead of raw `cmd.exe` — it routes through `explorer.exe` / PowerShell, which don't have the UNC issue |

## Tips

- **Smoke test first if you suspect interop is broken.** `check` launches Notepad and verifies it lands in your interactive session — this isolates "interop dead" from "specific app caught the Session-0 trap".
- **Browse the app list once, save the names you actually use.** `apps --search` finds anything by substring; once you know the canonical name, `open <name>` is faster than re-listing every time.
- **For repeated host operations in a script, prefer `admin-run` with a multi-line script.** One UAC prompt is much friendlier than ten.
- **`session` + `ps <name>` is the fastest "is this the Session-0 trap" diagnostic.** If the process is in Session 0 and yours is in Session ≥1, that's it.
- **AppUserModelIDs for UWP apps are stable across reboots,** so you can hardcode them in scripts: `python3 $TOOL open "Calculator"` resolves to `Microsoft.WindowsCalculator_8wekyb3d8bbwe!App` via the cache.
