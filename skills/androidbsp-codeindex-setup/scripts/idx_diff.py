#!/usr/bin/env python3
"""idx_diff.py — diff active_files.idx vs active_files.idx.prev,
group by subsystem (first two path segments)."""
from __future__ import annotations
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import find_bsp_root, make_parser, require_version

require_version("1.0.0")


def _bucket(path: str) -> str:
    parts = path.split('/', 2)
    return '/'.join(parts[:2]) if len(parts) >= 2 else path


def main():
    p = make_parser('Diff active_files.idx with previous version.')
    p.add_argument('--top', type=int, default=20,
                   help='show top N changed buckets (default 20)')
    args = p.parse_args()

    try:
        bsp_root = Path(args.root) if args.root else find_bsp_root()
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(2)

    cur = bsp_root / '.codenav' / 'active_files.idx'
    prev = bsp_root / '.codenav' / 'active_files.idx.prev'

    if not cur.exists():
        print(f"FAIL: {cur} not found", file=sys.stderr)
        sys.exit(2)
    if not prev.exists():
        print("INFO: no previous index — nothing to diff", file=sys.stderr)
        sys.exit(0)

    cur_set = set(cur.read_text().splitlines())
    prev_set = set(prev.read_text().splitlines())

    added = cur_set - prev_set
    removed = prev_set - cur_set

    print(f"=== changes ({len(added)} added, {len(removed)} removed) ===")

    # bucket aggregation
    bucket_changes = defaultdict(lambda: [0, 0])  # [added, removed]
    for f in added:
        bucket_changes[_bucket(f)][0] += 1
    for f in removed:
        bucket_changes[_bucket(f)][1] += 1

    sorted_buckets = sorted(bucket_changes.items(),
                            key=lambda x: -(x[1][0] + x[1][1]))

    print(f"\n=== top {args.top} subsystems by change ===")
    for bucket, (a, r) in sorted_buckets[:args.top]:
        sign = '+' if a > r else '-' if r > a else '='
        print(f"  {sign} {bucket:40s}  +{a:5d} / -{r:5d}")

    if args.json:
        import json as _json
        out = {
            'added_count': len(added),
            'removed_count': len(removed),
            'buckets': [{'bucket': b, 'added': a, 'removed': r}
                        for b, (a, r) in sorted_buckets],
        }
        print(_json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
