#!/usr/bin/env python3
"""compare_baseline.py — diff Phase 0 baseline vs Phase N rerun for atk validation.

Each baseline file has format:
    === ID: 01
    === DESC: ...
    === CMD: ...
    === START: ...
    === EXIT: 0
    === ELAPSED: 1.23s
    === STDERR: <stderr lines>
    === STDOUT:
    <actual output>

Compares ID-by-ID: line counts, exit codes, elapsed time, and reports
each as ✅ unchanged / ⚠️ improved / ❌ regressed.
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path


def parse_run(path: Path) -> dict:
    text = path.read_text(errors='replace')
    sections: dict[str, str] = {}
    cur_key = None
    cur_lines: list[str] = []
    for ln in text.splitlines():
        m = re.match(r'^=== ([A-Z]+):(.*)$', ln)
        if m:
            if cur_key is not None:
                sections[cur_key] = '\n'.join(cur_lines).strip()
            cur_key = m.group(1)
            cur_lines = [m.group(2).strip()] if m.group(2).strip() else []
        else:
            if cur_key is not None:
                cur_lines.append(ln)
    if cur_key is not None:
        sections[cur_key] = '\n'.join(cur_lines).strip()

    stdout = sections.get('STDOUT', '')
    return {
        'id': sections.get('ID', path.stem),
        'desc': sections.get('DESC', ''),
        'cmd': sections.get('CMD', ''),
        'exit': int((sections.get('EXIT', '0') or '0').strip()),
        'elapsed': _parse_elapsed(sections.get('ELAPSED', '0s')),
        'stderr': sections.get('STDERR', ''),
        'stdout': stdout,
        'stdout_lines': len([l for l in stdout.splitlines() if l.strip()]),
    }


def _parse_elapsed(s: str) -> float:
    m = re.match(r'([\d.]+)', s)
    return float(m.group(1)) if m else 0.0


def classify(before: dict, after: dict) -> tuple[str, str]:
    """Return (status, reason). status in {unchanged, improved, regressed}."""
    if before['exit'] == 0 and after['exit'] != 0:
        return 'regressed', f'exit {before["exit"]} → {after["exit"]}'
    if before['exit'] != 0 and after['exit'] == 0:
        return 'improved', f'exit {before["exit"]} → 0'

    bl, al = before['stdout_lines'], after['stdout_lines']
    if bl == 0 and al > 0:
        return 'improved', f'lines 0 → {al} (was empty, now hits)'
    if bl > 0 and al == 0:
        return 'regressed', f'lines {bl} → 0 (lost all hits)'
    if bl == 0 and al == 0:
        return 'unchanged', 'both empty'

    delta_pct = (al - bl) / bl if bl else 0
    if abs(delta_pct) <= 0.10:
        return 'unchanged', f'lines {bl}→{al} (within 10%)'
    if delta_pct > 0:
        return 'improved', f'lines {bl}→{al} (+{int(delta_pct*100)}%)'
    return 'regressed', f'lines {bl}→{al} ({int(delta_pct*100)}%)'


def main():
    ap = argparse.ArgumentParser(description='Compare baseline vs rerun outputs.')
    ap.add_argument('--before', type=Path, required=True,
                    help='baseline dir (e.g. skills/_validation/baseline_atk/)')
    ap.add_argument('--after', type=Path, required=True,
                    help='rerun dir (e.g. skills/_validation/run_2026-04-N/)')
    ap.add_argument('--verbose', '-v', action='store_true',
                    help='show stdout diff for changed queries')
    args = ap.parse_args()

    before_files = {f.stem: f for f in args.before.glob('*.txt')
                    if f.stem != 'PHASE0_NOTES'}
    after_files = {f.stem: f for f in args.after.glob('*.txt')}

    common = sorted(set(before_files) & set(after_files))
    only_before = sorted(set(before_files) - set(after_files))
    only_after = sorted(set(after_files) - set(before_files))

    counts = {'unchanged': 0, 'improved': 0, 'regressed': 0}
    rows = []
    for qid in common:
        b = parse_run(before_files[qid])
        a = parse_run(after_files[qid])
        status, reason = classify(b, a)
        counts[status] += 1
        rows.append((qid, status, reason, b, a))

    print(f"=== {len(common)} queries compared ===")
    print(f"✅  {counts['unchanged']:3d} unchanged")
    print(f"⚠️   {counts['improved']:3d} improved")
    print(f"❌  {counts['regressed']:3d} regressed")
    if only_before:
        print(f"\n⚠️  only in baseline: {only_before}")
    if only_after:
        print(f"\n⚠️  only in rerun: {only_after}")

    if counts['regressed'] > 0:
        print("\n=== regressions ===")
        for qid, status, reason, b, a in rows:
            if status == 'regressed':
                print(f"\n  [{qid}] {b['desc']}")
                print(f"    CMD: {b['cmd']}")
                print(f"    {reason}")
                print(f"    elapsed: {b['elapsed']:.2f}s → {a['elapsed']:.2f}s")
                if args.verbose:
                    print("    --- before stdout (first 5) ---")
                    for ln in b['stdout'].splitlines()[:5]:
                        print(f"      {ln}")
                    print("    --- after stdout (first 5) ---")
                    for ln in a['stdout'].splitlines()[:5]:
                        print(f"      {ln}")

    if counts['improved'] > 0:
        print("\n=== improvements ===")
        for qid, status, reason, b, a in rows:
            if status == 'improved':
                print(f"  [{qid}] {b['desc']}: {reason}")

    raise SystemExit(0 if counts['regressed'] == 0 else 1)


if __name__ == '__main__':
    main()
