#!/usr/bin/env python3
"""Scan a UART boot log for RK3568 boot milestones and report status."""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path

# Ordered milestones — each pattern signals one boot stage.
MILESTONES = [
    ("maskrom_or_ddr", re.compile(r"MaskRom|DDR Version|miniloader", re.I)),
    ("u_boot",        re.compile(r"U-Boot 20\d\d\.")),
    ("kernel",        re.compile(r"Linux version \d")),
    ("init_done",     re.compile(r"Boot is finished|init: Service '(?:bootanim|servicemanager)' .* started")),
]

FAIL_PATTERNS = [
    ("kernel_panic", re.compile(r"Kernel panic")),
    ("oops",         re.compile(r"Unable to handle kernel paging request")),
    ("watchdog",     re.compile(r"watchdog: BUG: soft lockup")),
]


def scan(text: str) -> dict:
    hit = []
    missed = []
    for name, pat in MILESTONES:
        if pat.search(text):
            hit.append(name)
        else:
            missed.append(name)
    fail = [n for n, p in FAIL_PATTERNS if p.search(text)]
    status = "ok" if not missed and not fail else "fail"
    return {
        "status": status,
        "milestones_hit": hit,
        "milestones_missed": missed,
        "fail_reasons": fail,
    }


def tail(text: str, n: int = 30) -> str:
    return "\n".join(text.splitlines()[-n:])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--json", action="store_true",
                    help="emit single-line JSON to stdout")
    args = ap.parse_args()

    text = Path(args.log).read_text(encoding="utf-8", errors="replace")
    res = scan(text)

    if args.json:
        print(json.dumps(res))
    else:
        if res["status"] == "ok":
            print("[boot_verify] ✅ FLASH SUCCESS — all milestones hit")
        else:
            print("[boot_verify] ❌ FAIL")
            print(f"  hit:    {res['milestones_hit']}")
            print(f"  missed: {res['milestones_missed']}")
            if res["fail_reasons"]:
                print(f"  errors: {res['fail_reasons']}")
            print("--- last 30 lines ---")
            print(tail(text))

    return 0 if res["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
