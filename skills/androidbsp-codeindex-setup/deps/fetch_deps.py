#!/usr/bin/env python3
"""
fetch_deps.py — Populate deps/packages/ with .deb files for offline install.

Run this once on a machine that has network + matching Ubuntu/Debian
release. The resulting .deb cache is then shipped with the skill and
consumed by install_tools.py --offline on air-gapped targets.

Strategy (recursive closure, not just the named packages):
  1. apt-get update (skippable via --no-update)
  2. For each target package, use `apt-cache depends --recurse` to walk
     the full transitive dependency tree, dropping weak links (Recommends/
     Suggests/...). This gives the *real* set of .deb files needed so
     `dpkg -i packages/*.deb` on a minimal target actually works.
  3. `apt-get download` each package in that set. Already-present .debs
     in the cache are skipped.

Notes:
  * Output is arch-specific (amd64 != arm64). Re-run on each target arch.
  * We never install anything system-wide from here; dpkg is not invoked.
  * Core libs (libc6, libgcc-s1, libstdc++6) are almost always present on
     any Ubuntu target — they're still included so dpkg -i never fails on
     a missing dep.
  * Typical cache size: 40-120 MB depending on release.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

TARGET_PACKAGES = [
    "ripgrep",
    "fd-find",
    "global",
    "universal-ctags",
    "clangd",
    "fzf",
]

SCRIPT_DIR = Path(__file__).resolve().parent
PKG_CACHE  = SCRIPT_DIR / "packages"


def run(cmd: list[str], *, check: bool = True) -> None:
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=check, text=True)


def sudo(cmd: list[str]) -> list[str]:
    if os.geteuid() == 0:
        return cmd
    return ["sudo", *cmd]


def resolve_closure(packages: list[str]) -> list[str]:
    """Return the transitive hard-dep closure for `packages` as apt package names."""
    cmd = [
        "apt-cache", "depends", "--recurse",
        "--no-recommends", "--no-suggests", "--no-conflicts",
        "--no-breaks", "--no-replaces", "--no-enhances",
        *packages,
    ]
    print(f"  $ {' '.join(cmd)}")
    out = subprocess.check_output(cmd, text=True)
    names = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("Depends:", "PreDepends:", "|Depends:")):
            # dep lines like "Depends: libc6" — grab the name
            if ":" in line:
                name = line.split(":", 1)[1].strip()
                # strip virtual-package markers like <pkg>
                name = name.strip("<>")
                if name and not name.startswith("|"):
                    names.add(name)
            continue
        # top-level package names (no leading whitespace originally, no colon)
        if ":" not in line and not line.startswith(" "):
            names.add(line)
    # apt-cache output also contains the requested packages at root level
    names.update(packages)
    # filter out virtual packages that have no candidate
    real = []
    for n in sorted(names):
        r = subprocess.run(
            ["apt-cache", "show", n],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            real.append(n)
    return real


def fix_ownership(path: Path) -> None:
    """apt-get download under sudo leaves files owned by root; chown back to caller."""
    uid = os.environ.get("SUDO_UID") or str(os.getuid())
    gid = os.environ.get("SUDO_GID") or str(os.getgid())
    run(sudo(["chown", "-R", f"{uid}:{gid}", str(path)]), check=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-update", action="store_true",
                    help="skip apt-get update (use existing lists)")
    ap.add_argument("--clean", action="store_true",
                    help="wipe deps/packages before fetching")
    args = ap.parse_args()

    if args.clean and PKG_CACHE.exists():
        print(f"[clean] removing {PKG_CACHE}")
        # may be root-owned from previous run
        subprocess.run(sudo(["rm", "-rf", str(PKG_CACHE)]), check=False)

    PKG_CACHE.mkdir(parents=True, exist_ok=True)

    if not args.no_update:
        run(sudo(["apt-get", "update"]), check=False)

    print(f"[resolve] computing dep closure for: {' '.join(TARGET_PACKAGES)}")
    closure = resolve_closure(TARGET_PACKAGES)
    print(f"[resolve] {len(closure)} packages in closure")

    # apt-get download must run in a writable CWD (it writes .debs to $PWD).
    # Download in chunks — long arg lists occasionally confuse apt.
    os.chdir(PKG_CACHE)
    CHUNK = 40
    for i in range(0, len(closure), CHUNK):
        chunk = closure[i:i + CHUNK]
        run(["apt-get", "download", *chunk], check=False)

    fix_ownership(PKG_CACHE)

    debs = sorted(PKG_CACHE.glob("*.deb"))
    total_mb = sum(p.stat().st_size for p in debs) / 1024 / 1024
    print(f"\n✓ cached {len(debs)} packages ({total_mb:.1f} MB) in {PKG_CACHE}")
    missing = [p for p in closure if not list(PKG_CACHE.glob(f"{p}_*.deb"))]
    if missing:
        print(f"⚠ {len(missing)} packages in closure missing a .deb:")
        for m in missing:
            print(f"    {m}")
    return 0 if debs else 1


if __name__ == "__main__":
    sys.exit(main())
