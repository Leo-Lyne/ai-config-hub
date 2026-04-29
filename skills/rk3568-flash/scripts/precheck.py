#!/usr/bin/env python3
"""Pre-flash checks for rk3568-flash skill (item a in design)."""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_parameter as lp

REQUIRED_FOR_FULL = ["parameter.txt", "MiniLoaderAll.bin"]


def err(msg: str) -> None:
    print(f"[precheck] ERROR: {msg}", file=sys.stderr)


def check_files(image_dir: Path, mode: str, parts: list[str]) -> bool:
    if not image_dir.is_dir():
        err(f"image-dir not found: {image_dir}")
        return False
    if mode == "full":
        for f in REQUIRED_FOR_FULL:
            if not (image_dir / f).is_file():
                err(f"required file missing: {f}")
                return False
    # validate per-partition images
    if mode == "full":
        param = lp.parse(image_dir / "parameter.txt")
        wanted = [p for p in param.partitions if p != "userdata"]
    else:
        wanted = parts
    for name in wanted:
        candidate = image_dir / f"{name}.img"
        if not candidate.is_file():
            err(f"image missing for partition '{name}': {candidate}")
            return False
        if candidate.stat().st_size == 0:
            err(f"image zero-size for partition '{name}': {candidate}")
            return False
    return True


def check_parameter(image_dir: Path) -> tuple[bool, lp.Parameter | None]:
    if not image_dir.is_dir():
        err(f"image-dir not found: {image_dir}")
        return False, None
    pf = image_dir / "parameter.txt"
    if not pf.is_file():
        err(f"parameter.txt missing in {image_dir}")
        return False, None
    p = lp.parse(pf)
    if not p.is_magic_valid():
        err(f"parameter.txt MAGIC invalid: 0x{p.magic:08X} (expected 0x5041524B)")
        return False, None
    if not p.machine_model:
        err("parameter.txt missing MACHINE_MODEL")
        return False, None
    if not p.partitions:
        err("parameter.txt CMDLINE has no partitions")
        return False, None
    return True, p


def check_adb_model(expected: str, force: bool) -> bool:
    try:
        r = subprocess.run(
            ["adb", "shell", "getprop", "ro.product.model"],
            capture_output=True, text=True, timeout=5)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        err(f"adb not usable: {e}")
        return False
    if r.returncode != 0:
        print("[precheck] adb offline (will use button-press path)")
        return True  # not fatal: skipping model check
    actual = r.stdout.strip()
    if actual != expected:
        err(f"device model mismatch: device='{actual}' parameter.txt='{expected}'")
        if force:
            print("[precheck] --force-mismatch supplied: continuing despite mismatch")
            return True
        err("re-run with --force-mismatch to override")
        return False
    print(f"[precheck] device model OK: {actual}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-dir", required=True)
    ap.add_argument("--mode", choices=["full", "parts"], required=True)
    ap.add_argument("--parts", nargs="*", default=[])
    ap.add_argument("--no-adb-check", action="store_true")
    ap.add_argument("--force-mismatch", action="store_true")
    args = ap.parse_args()

    image_dir = Path(args.image_dir)

    ok_param, param = check_parameter(image_dir)
    if not ok_param:
        return 1

    if not check_files(image_dir, args.mode, args.parts):
        return 1

    if not args.no_adb_check:
        if not check_adb_model(param.machine_model, args.force_mismatch):
            return 1

    print("[precheck] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
