#!/usr/bin/env python3
"""Compute partitions changed since last flash and (optionally) flash them."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_STATE = Path.home() / ".cache" / "rk3568-flash" / "last_flash.json"


def load_state(p: Path) -> dict | None:
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def save_state(p: Path, state: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def scan_imgs(image_dir: Path) -> dict[str, dict]:
    out = {}
    for f in image_dir.glob("*.img"):
        if f.name == "userdata.img":
            continue
        out[f.name] = {"mtime": int(f.stat().st_mtime)}
    return out


def diff(prev: dict, now: dict) -> list[str]:
    """Return partition names (without .img) whose mtime is newer or which are new."""
    changed = []
    for name, meta in now.items():
        prev_meta = prev.get(name)
        if prev_meta is None or meta["mtime"] > prev_meta.get("mtime", 0):
            changed.append(name.removesuffix(".img"))
    return sorted(changed)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-dir", required=True)
    ap.add_argument("--state-file", default=str(DEFAULT_STATE))
    ap.add_argument("--print-only", action="store_true",
                    help="just print the changed partition list, do not flash")
    ap.add_argument("--flash-cmd", default="",
                    help="command to invoke for flashing, will receive partitions as args")
    args = ap.parse_args()

    image_dir = Path(args.image_dir)
    state_file = Path(args.state_file)

    if not image_dir.is_dir():
        print(f"image-dir not found: {image_dir}", file=sys.stderr)
        return 1

    now = scan_imgs(image_dir)
    if not now:
        print(f"no .img files in {image_dir}", file=sys.stderr)
        return 1

    state = load_state(state_file)
    if state is None or "files" not in state:
        print("FIRST_RUN: no flash history. Run /rk3568-flash full first to create a baseline.")
        return 3

    changed = diff(state["files"], now)
    if not changed:
        print("NO_CHANGES: nothing to do.")
        return 0

    print("CHANGED: " + " ".join(changed))

    if args.print_only:
        return 0

    if not args.flash_cmd:
        print("--flash-cmd not set; rerun with the flash entrypoint", file=sys.stderr)
        return 1

    cmd = [args.flash_cmd, *changed]
    r = subprocess.run(cmd)
    if r.returncode != 0:
        return r.returncode

    save_state(state_file, {
        "image_dir": str(image_dir),
        "files": now,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
