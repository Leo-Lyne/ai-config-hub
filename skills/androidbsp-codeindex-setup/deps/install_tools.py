#!/usr/bin/env python3
"""
install_tools.py — Install/verify BSP code-index toolchain.

Behaviour:
  * Detects which tools are missing (rg / fd / gtags / ctags / clangd / fzf).
  * Probes network (apt repo reachability).
  * Online : apt-get install <missing>; for already-installed pkgs,
             runs apt-get --just-print upgrade to surface newer versions.
  * Offline: dpkg -i deps/packages/*.deb, then apt-get install -f to fix
             any residual dependency issues from the local cache.
  * Post-install: creates /usr/local/bin/fd symlink if fdfind exists,
                  verifies every tool can be invoked.

Usage:
  python3 install_tools.py              # auto (online if possible, else offline)
  python3 install_tools.py --offline    # force offline
  python3 install_tools.py --online     # force online (fail if no network)
  python3 install_tools.py --check-only # report status, no changes
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# ── config ────────────────────────────────────────────────────────────────

# tool binary name  →  apt package name
TOOLS: dict[str, str] = {
    "rg":     "ripgrep",
    "fd":     "fd-find",          # binary is fdfind on debian/ubuntu
    "gtags":  "global",
    "ctags":  "universal-ctags",
    "clangd": "clangd",
    "fzf":    "fzf",
}

SCRIPT_DIR = Path(__file__).resolve().parent
PKG_CACHE  = SCRIPT_DIR / "packages"
NET_PROBES = (
    "http://archive.ubuntu.com",
    "http://security.ubuntu.com",
    "http://ports.ubuntu.com",
)

# ── utilities ─────────────────────────────────────────────────────────────

def run(cmd: list[str], *, check: bool = True, capture: bool = False,
        env: dict | None = None) -> subprocess.CompletedProcess:
    """Thin wrapper that prints the command and streams output by default."""
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(
        cmd, check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=env,
    )


def sudo(cmd: list[str]) -> list[str]:
    """Prepend sudo when not already root."""
    if os.geteuid() == 0:
        return cmd
    return ["sudo", *cmd]


def have(binary: str) -> str | None:
    """Return the resolved path for `binary` if on PATH, else None."""
    # fd-find ships the binary as `fdfind` on debian/ubuntu
    if binary == "fd":
        return shutil.which("fd") or shutil.which("fdfind")
    return shutil.which(binary)


def network_reachable(timeout: float = 3.0) -> bool:
    for url in NET_PROBES:
        try:
            urllib.request.urlopen(url, timeout=timeout)
            return True
        except Exception:
            continue
    return False


def tool_status() -> dict[str, str | None]:
    return {t: have(t) for t in TOOLS}


def print_status(status: dict[str, str | None]) -> None:
    for tool, path in status.items():
        mark = "✓" if path else "✗"
        print(f"  {mark} {tool:<7} {path or '(missing)'}")


# ── install strategies ───────────────────────────────────────────────────

def install_online(missing_pkgs: list[str]) -> None:
    if not missing_pkgs:
        print("[online] nothing to install")
        return
    print(f"[online] apt-get install: {' '.join(missing_pkgs)}")
    run(sudo(["apt-get", "update"]), check=False)
    run(sudo(["apt-get", "install", "-y", *missing_pkgs]))


def check_updates(installed_pkgs: list[str]) -> None:
    """Report (but don't apply) available upgrades for already-installed pkgs."""
    if not installed_pkgs:
        return
    print("[online] checking for updates to already-installed tools…")
    try:
        result = run(
            ["apt-get", "--just-print", "upgrade", *installed_pkgs],
            check=False, capture=True,
        )
    except FileNotFoundError:
        return
    out = (result.stdout or "") + (result.stderr or "")
    upgrades = [line for line in out.splitlines() if line.startswith("Inst ")]
    if upgrades:
        print("  → updates available:")
        for line in upgrades:
            print(f"    {line}")
        print("    run: sudo apt-get upgrade " + " ".join(installed_pkgs))
    else:
        print("  → everything up to date")


def install_offline(missing_pkgs: list[str]) -> None:
    if not missing_pkgs:
        print("[offline] nothing to install")
        return
    debs = sorted(PKG_CACHE.glob("*.deb"))
    if not debs:
        sys.exit(
            f"[offline] no .deb files in {PKG_CACHE}. "
            f"Populate it on a machine with network access:\n"
            f"    python3 {SCRIPT_DIR}/fetch_deps.py"
        )
    print(f"[offline] installing {len(debs)} .deb files from {PKG_CACHE}")
    run(sudo(["dpkg", "-i", *map(str, debs)]), check=False)
    # fix any broken deps using the local cache only
    run(
        sudo([
            "apt-get", "install", "-f", "-y",
            "-o", "Dir::Cache::Archives=" + str(PKG_CACHE),
        ]),
        check=False,
    )


def ensure_fd_symlink() -> None:
    """apt's fd-find installs /usr/bin/fdfind; many users expect `fd`."""
    if shutil.which("fd"):
        return
    fdfind = shutil.which("fdfind")
    if not fdfind:
        return
    target = Path("/usr/local/bin/fd")
    print(f"[post] creating {target} -> {fdfind}")
    run(sudo(["ln", "-sf", fdfind, str(target)]), check=False)


def verify(status: dict[str, str | None]) -> bool:
    ok = True
    for tool in TOOLS:
        path = have(tool)
        status[tool] = path
        if not path:
            ok = False
    print("\nfinal status:")
    print_status(status)
    return ok


# ── main ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="force offline install from deps/packages")
    ap.add_argument("--online", action="store_true",
                    help="force online install, fail if no network")
    ap.add_argument("--check-only", action="store_true",
                    help="only report current tool status")
    args = ap.parse_args()

    print("== BSP code-index toolchain ==")
    status = tool_status()
    print_status(status)

    missing = [TOOLS[t] for t, p in status.items() if p is None]
    present = [TOOLS[t] for t, p in status.items() if p is not None]

    if args.check_only:
        return 0 if not missing else 1

    if not missing and not args.online:
        print("\nall tools present.")
        # still useful to surface upgrades when the user has network
        if network_reachable():
            check_updates(present)
        return 0

    # decide path
    online = not args.offline and (args.online or network_reachable())
    if args.online and not online:
        sys.exit("--online requested but no network reachable")

    print(f"\nmissing: {missing or '(none)'}")
    print(f"mode: {'online' if online else 'offline'}\n")

    if online:
        install_online(missing)
        check_updates(present)
    else:
        install_offline(missing)

    ensure_fd_symlink()

    if not verify(tool_status()):
        print("\n✗ some tools still missing — inspect errors above.", file=sys.stderr)
        return 1
    print("\n✓ all tools available.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
