#!/usr/bin/env python3
"""wsl-host-control: drive the Windows host from inside WSL.

Dispatches sub-commands to the host via Windows interop. Privileged operations
go through `Start-Process powershell -Verb RunAs` with output captured via a
temp file (one UAC prompt per call).

App discovery uses `Get-StartApps`, which enumerates everything that appears in
the Start Menu (UWP/Store + Win32 shortcuts), and caches the result so name-
based launches are instant.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "wsl-host-control"
APPS_CACHE = CACHE_DIR / "apps.json"
APPS_CACHE_TTL_SECONDS = 24 * 3600

CHROME_PROFILE_DIR_WIN = r"%LOCALAPPDATA%\ChromeTemp-WslHostControl"


# --------------------------------------------------------------------------- #
# Low-level: PowerShell invocation                                             #
# --------------------------------------------------------------------------- #

_PS_PREAMBLE = (
    # Suppress "Preparing modules for first use." and other progress streams,
    # which otherwise leak through stdout as CLIXML when stdio is redirected.
    "$ProgressPreference='SilentlyContinue';"
    # Force UTF-8 stdout so non-ASCII (e.g. Chinese app names) round-trip cleanly.
    # `$null =` suppresses the assignment's echo-back, which would otherwise
    # corrupt the first line of output.
    "$null=[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
)


def _encode_ps(script: str) -> str:
    return base64.b64encode((_PS_PREAMBLE + script).encode("utf-16-le")).decode("ascii")


def ps_run(script: str, capture: bool = True, check: bool = True) -> str:
    """Run a PowerShell snippet (non-elevated). Returns stdout.

    `-OutputFormat Text` is critical: without it, PowerShell serializes
    objects/errors as CLIXML when stdio is redirected, and that XML leaks into
    output. With it, errors arrive as plain text (or just exit-code != 0).

    PowerShell's exit code is unreliable for our purposes — even
    `-ErrorAction SilentlyContinue` errors propagate to $LASTEXITCODE under
    `-NonInteractive -EncodedCommand`. So `check` only fires when stderr has
    real content; a silent rc!=0 is treated as success.
    """
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-OutputFormat", "Text",
        "-EncodedCommand",
        _encode_ps(script),
    ]
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if check and r.returncode != 0 and (r.stderr or "").strip():
        sys.stderr.write(r.stderr)
        sys.exit(r.returncode)
    return r.stdout


def ps_run_admin(script: str) -> str:
    """Run a PowerShell snippet elevated. Returns stdout (and stderr).

    Uses a Windows temp file because `-Verb RunAs` cannot redirect output
    directly. The user sees one UAC prompt per call.
    """
    # Reserve a temp file path on the Windows side that both shells can see.
    tmp = ps_run(
        "[IO.Path]::GetTempFileName()", check=True
    ).strip()
    if not tmp:
        sys.exit("could not allocate temp file")

    # Wrap user script in a try/catch + redirect everything (stdout/stderr/etc.) to $tmp.
    # The `*>` operator catches all streams (1=stdout, 2=stderr, 3=warning, ...).
    inner = (
        f'try {{ & {{ {script} }} *> "{tmp}" }} '
        f'catch {{ $_ | Out-File -FilePath "{tmp}" -Encoding utf8 }}'
    )
    encoded = _encode_ps(inner)

    outer = (
        'Start-Process powershell -Verb RunAs -WindowStyle Hidden -Wait '
        f'-ArgumentList @("-NoProfile","-NonInteractive","-EncodedCommand","{encoded}")'
    )
    ps_run(outer, check=True)

    out = ps_run(
        f'Get-Content -Raw -Encoding utf8 "{tmp}"; Remove-Item -Force "{tmp}"',
        check=False,
    )
    return out or ""


def ps_quote(s: str) -> str:
    """Quote a Python string as a PowerShell single-quoted literal."""
    return "'" + s.replace("'", "''") + "'"


# --------------------------------------------------------------------------- #
# App discovery                                                                #
# --------------------------------------------------------------------------- #

def load_apps(force_refresh: bool = False) -> list[dict]:
    """Return the cached list of installed apps (Name + AppID).

    Refreshes when older than TTL or when the cache is missing/empty.
    """
    if (
        not force_refresh
        and APPS_CACHE.exists()
        and (time.time() - APPS_CACHE.stat().st_mtime) < APPS_CACHE_TTL_SECONDS
    ):
        try:
            data = json.loads(APPS_CACHE.read_text())
            if data:
                return data
        except Exception:
            pass
    return refresh_apps()


def refresh_apps() -> list[dict]:
    """Enumerate Start Menu apps via Get-StartApps and cache the result."""
    raw = ps_run(
        "Get-StartApps | ConvertTo-Json -Compress",
        check=True,
    ).strip()
    if not raw:
        apps: list[dict] = []
    else:
        parsed = json.loads(raw)
        # ConvertTo-Json yields a single object when there's exactly one app.
        if isinstance(parsed, dict):
            parsed = [parsed]
        apps = [{"name": p["Name"], "appid": p["AppID"]} for p in parsed]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    APPS_CACHE.write_text(json.dumps(apps, ensure_ascii=False, indent=2))
    return apps


def find_apps(query: str, apps: list[dict] | None = None) -> list[dict]:
    """Return apps whose name OR AppID contains the query (case-insensitive).

    Matching against AppID lets users find apps by their English package name
    even on non-English systems (e.g. searching "calculator" matches the
    Chinese-named "计算器" via its AppID `Microsoft.WindowsCalculator_...`).
    """
    apps = apps if apps is not None else load_apps()
    q = query.casefold()
    return [
        a for a in apps
        if q in a["name"].casefold() or q in a["appid"].casefold()
    ]


# --------------------------------------------------------------------------- #
# Subcommand handlers                                                          #
# --------------------------------------------------------------------------- #

def cmd_apps(args: argparse.Namespace) -> int:
    apps = refresh_apps() if args.refresh else load_apps()
    if args.search:
        apps = find_apps(args.search, apps)
    if args.json:
        print(json.dumps(apps, ensure_ascii=False, indent=2))
    else:
        width = max((len(a["name"]) for a in apps), default=0)
        for a in apps:
            print(f"{a['name']:<{width}}  {a['appid']}")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    """Launch an app by name (substring match against the cached app list)."""
    matches = find_apps(args.name)
    if not matches:
        sys.exit(f"no installed app matches '{args.name}'. try `apps refresh` or `apps search`.")
    if len(matches) > 1 and not args.first:
        # Prefer an exact match if present.
        exact = [a for a in matches if a["name"].casefold() == args.name.casefold()]
        if len(exact) == 1:
            matches = exact
        else:
            print("multiple matches — pass --first to pick the top one, or be more specific:")
            for a in matches[:10]:
                print(f"  - {a['name']}")
            return 1
    app = matches[0]
    appid = app["appid"]

    # The AppsFolder shell namespace handles every entry Get-StartApps returns —
    # UWP packages, Win32 shortcuts under any KnownFolderID prefix, even URL
    # entries — uniformly via ShellExecute. Use it when no args are needed.
    if not args.args:
        subprocess.Popen(
            ["explorer.exe", f"shell:AppsFolder\\{appid}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        # Args only make sense for Win32 binaries; route through Start-Process
        # so they reach argv. UWP apps will silently ignore the args here.
        joined = " ".join(ps_quote(a) for a in args.args)
        ps = (
            f"Start-Process -FilePath {ps_quote(appid)} "
            f"-ArgumentList @({joined})"
        )
        ps_run(ps, capture=False, check=False)

    print(f"launched: {app['name']}")
    return 0


def cmd_chrome(args: argparse.Namespace) -> int:
    """Open Chrome with an isolated profile to dodge the Session-0 trap."""
    chrome = _find_chrome()
    if not chrome:
        sys.exit("chrome.exe not found in standard locations")
    cmd = [
        chrome,
        f"--user-data-dir={CHROME_PROFILE_DIR_WIN}",
        "--new-window",
    ]
    if args.url:
        cmd.append(args.url)
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("chrome launched (isolated profile)")
    return 0


def _find_chrome() -> str | None:
    candidates = [
        "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
        "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    # Per-user install
    users = Path("/mnt/c/Users")
    if users.exists():
        for u in users.iterdir():
            p = u / "AppData/Local/Google/Chrome/Application/chrome.exe"
            if p.exists():
                return str(p)
    return None


def cmd_url(args: argparse.Namespace) -> int:
    """Open URL or file in the default associated app."""
    target = args.target
    # If it's a local Linux path, translate to Windows.
    if target.startswith("/") and not target.startswith("//"):
        target = subprocess.run(
            ["wslpath", "-w", target], capture_output=True, text=True, check=True
        ).stdout.strip()
    subprocess.Popen(
        ["explorer.exe", target],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0  # explorer.exe's exit code 1 is a normal lie; ignore it


def cmd_settings(args: argparse.Namespace) -> int:
    """Open a Settings page via the ms-settings: URI scheme."""
    page = args.page
    if not page.startswith("ms-settings:"):
        page = f"ms-settings:{page}"
    subprocess.Popen(
        ["explorer.exe", page],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


def cmd_clip_copy(args: argparse.Namespace) -> int:
    if args.text is not None:
        data = args.text.encode("utf-8")
    else:
        data = sys.stdin.buffer.read()
    p = subprocess.Popen(["clip.exe"], stdin=subprocess.PIPE)
    p.communicate(data)
    return p.returncode or 0


def cmd_clip_paste(args: argparse.Namespace) -> int:
    out = ps_run("Get-Clipboard")
    sys.stdout.write(out.replace("\r\n", "\n").replace("\r", ""))
    return 0


def cmd_notify(args: argparse.Namespace) -> int:
    """Toast notification via BurntToast; falls back to msg.exe."""
    title = args.title or "WSL"
    body = args.body or ""
    script = (
        "if (Get-Module -ListAvailable BurntToast) {"
        "  Import-Module BurntToast;"
        f"  New-BurntToastNotification -Text {ps_quote(title)}, {ps_quote(body)};"
        "} else {"
        "  $null = (msg.exe $env:USERNAME "
        f'   ({ps_quote(title + ": " + body)})) 2>$null;'
        "}"
    )
    ps_run(script, check=False)
    return 0


def cmd_ps(args: argparse.Namespace) -> int:
    # -ErrorAction SilentlyContinue: empty match is a normal result, not an error.
    filt = (
        f"-Name {ps_quote(args.filter)} -ErrorAction SilentlyContinue"
        if args.filter else ""
    )
    out = ps_run(
        f"Get-Process {filt} | Sort-Object SessionId, Name | "
        "Select-Object Id, SessionId, ProcessName, "
        "@{N='WS_MB';E={[int]($_.WorkingSet64/1MB)}} | "
        "Format-Table -AutoSize | Out-String -Width 200"
    )
    if out.strip():
        print(out)
    elif args.filter:
        print(f"no process matches '{args.filter}'")
    return 0


def cmd_kill(args: argparse.Namespace) -> int:
    target = args.target
    if target.isdigit():
        cmd = f"Stop-Process -Id {int(target)} -Force"
    else:
        cmd = f"Stop-Process -Name {ps_quote(target)} -Force"
    out = ps_run_admin(cmd) if args.admin else ps_run(cmd, check=False)
    if out.strip():
        print(out)
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    out = ps_run(
        "Write-Output '== query session ==';"
        "(query.exe session) -join \"`n\";"
        "Write-Output '';"
        "Write-Output '== current shell session ==';"
        "Write-Output \"PID=$PID  SessionId=$((Get-Process -Id $PID).SessionId)\""
    )
    print(out)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Smoke-test interop: confirm a GUI program lands on the user desktop."""
    print("launching notepad.exe — close it after it appears.")
    subprocess.Popen(
        ["notepad.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(1.0)
    out = ps_run(
        "(Get-Process notepad -ErrorAction SilentlyContinue | "
        "Select-Object -First 1 -ExpandProperty SessionId) -as [string]"
    ).strip()
    me = ps_run(
        "(Get-Process -Id $PID).SessionId"
    ).strip()
    print(f"notepad SessionId = {out!r}; powershell SessionId = {me!r}")
    if not out:
        print("WARN: no notepad found — interop may be broken.")
        return 1
    if out != me:
        print("WARN: notepad launched in a different session than this shell.")
        print("      this is the Session-0 trap; GUI windows won't be visible.")
        return 2
    print("OK: interop healthy, GUI launches in your session.")
    return 0


def cmd_path_w(args: argparse.Namespace) -> int:
    r = subprocess.run(
        ["wslpath", "-w", args.path], capture_output=True, text=True, check=True
    )
    sys.stdout.write(r.stdout)
    return 0


def cmd_path_u(args: argparse.Namespace) -> int:
    r = subprocess.run(
        ["wslpath", "-u", args.path], capture_output=True, text=True, check=True
    )
    sys.stdout.write(r.stdout)
    return 0


def cmd_admin_run(args: argparse.Namespace) -> int:
    """Run an arbitrary PowerShell command elevated; print captured output."""
    out = ps_run_admin(args.script)
    sys.stdout.write(out)
    return 0


def cmd_svc(args: argparse.Namespace) -> int:
    name = args.name
    action = args.action
    if action == "status":
        out = ps_run(
            f"Get-Service -Name {ps_quote(name)} | Format-List Name,Status,StartType,DisplayName | Out-String"
        )
        print(out)
        return 0
    verb = {"start": "Start-Service", "stop": "Stop-Service", "restart": "Restart-Service"}[action]
    out = ps_run_admin(f"{verb} -Name {ps_quote(name)} -PassThru | Format-List Name,Status | Out-String")
    print(out)
    return 0


def cmd_reg(args: argparse.Namespace) -> int:
    op = args.op
    key = args.key
    needs_admin = key.upper().startswith("HKLM") or key.upper().startswith("HKEY_LOCAL_MACHINE")

    if op == "get":
        ps = (
            f"reg.exe query {ps_quote(key)}"
            + (f" /v {ps_quote(args.value)}" if args.value else "")
        )
    elif op == "set":
        if not args.value or args.data is None:
            sys.exit("reg set requires --value and --data")
        ps = (
            f"reg.exe add {ps_quote(key)} /v {ps_quote(args.value)} "
            f"/t {args.type} /d {ps_quote(args.data)} /f"
        )
    elif op == "del":
        if args.value:
            ps = f"reg.exe delete {ps_quote(key)} /v {ps_quote(args.value)} /f"
        else:
            ps = f"reg.exe delete {ps_quote(key)} /f"
    else:
        sys.exit(f"unknown reg op: {op}")

    out = ps_run_admin(ps) if (needs_admin and op != "get") else ps_run(ps, check=False)
    sys.stdout.write(out)
    return 0


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wsl-host-control",
        description="Drive the Windows host from WSL.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # apps
    sp = sub.add_parser("apps", help="list installed Start-Menu apps")
    sp.add_argument("--refresh", action="store_true", help="force re-enumerate")
    sp.add_argument("--search", help="filter by substring (case-insensitive)")
    sp.add_argument("--json", action="store_true", help="emit JSON")
    sp.set_defaults(func=cmd_apps)

    # open
    sp = sub.add_parser("open", help="launch an app by name")
    sp.add_argument("name", help="app name or substring")
    sp.add_argument("args", nargs="*", help="args (Win32 only; UWP ignores)")
    sp.add_argument("--first", action="store_true", help="auto-pick first match")
    sp.set_defaults(func=cmd_open)

    # chrome
    sp = sub.add_parser("chrome", help="open Chrome (isolated profile, dodges Session-0)")
    sp.add_argument("url", nargs="?", help="optional URL")
    sp.set_defaults(func=cmd_chrome)

    # url / file
    sp = sub.add_parser("url", help="open URL or file in default app")
    sp.add_argument("target", help="URL or local file path")
    sp.set_defaults(func=cmd_url)

    # settings
    sp = sub.add_parser("settings", help="open Settings page (e.g. display, network)")
    sp.add_argument("page", help="ms-settings: URI suffix, or full ms-settings:* URI")
    sp.set_defaults(func=cmd_settings)

    # clip
    sp = sub.add_parser("clip-copy", help="copy stdin (or --text) to Windows clipboard")
    sp.add_argument("--text", help="text to copy (defaults to stdin)")
    sp.set_defaults(func=cmd_clip_copy)

    sp = sub.add_parser("clip-paste", help="print Windows clipboard contents")
    sp.set_defaults(func=cmd_clip_paste)

    # notify
    sp = sub.add_parser("notify", help="toast notification on Windows")
    sp.add_argument("title", nargs="?", default="WSL")
    sp.add_argument("body", nargs="?", default="")
    sp.set_defaults(func=cmd_notify)

    # ps / kill
    sp = sub.add_parser("ps", help="list Windows processes (with SessionId)")
    sp.add_argument("filter", nargs="?", help="process name (wildcards ok)")
    sp.set_defaults(func=cmd_ps)

    sp = sub.add_parser("kill", help="terminate a Windows process")
    sp.add_argument("target", help="PID or process name")
    sp.add_argument("--admin", action="store_true", help="elevate via UAC")
    sp.set_defaults(func=cmd_kill)

    # session / check
    sp = sub.add_parser("session", help="show Windows sessions and our session id")
    sp.set_defaults(func=cmd_session)

    sp = sub.add_parser("check", help="smoke-test interop and Session-0 detection")
    sp.set_defaults(func=cmd_check)

    # path
    sp = sub.add_parser("path-w", help="convert Linux path → Windows path")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_path_w)

    sp = sub.add_parser("path-u", help="convert Windows path → Linux path")
    sp.add_argument("path")
    sp.set_defaults(func=cmd_path_u)

    # admin-run
    sp = sub.add_parser(
        "admin-run",
        help="run an arbitrary PowerShell snippet elevated (single UAC prompt)",
    )
    sp.add_argument("script", help="PowerShell command(s)")
    sp.set_defaults(func=cmd_admin_run)

    # svc
    sp = sub.add_parser("svc", help="manage a Windows service")
    sp.add_argument("name")
    sp.add_argument(
        "action", choices=["status", "start", "stop", "restart"], default="status", nargs="?"
    )
    sp.set_defaults(func=cmd_svc)

    # reg
    sp = sub.add_parser("reg", help="registry get/set/del (HKLM auto-elevates)")
    sp.add_argument("op", choices=["get", "set", "del"])
    sp.add_argument("key", help='e.g. HKCU\\Software\\MyApp')
    sp.add_argument("--value", help="value name under the key")
    sp.add_argument("--data", help="data to write (set only)")
    sp.add_argument("--type", default="REG_SZ", help="REG_SZ, REG_DWORD, etc.")
    sp.set_defaults(func=cmd_reg)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
