#!/usr/bin/env python3
"""apex_locate.py — locate APEX module definitions and contents.

Inputs: APEX module name (e.g. com.android.media) or library/binary name
        contained in an APEX.

Outputs:
  - APEX-DEF: Soong apex {} block (Android.bp)
  - APEX-MANIFEST: apex_manifest.json/.pb
  - APEX-INSTALL: /apex/<name>/ install path under out/
  - APEX-CONTENT: which APEX(es) contain a given lib/binary (if reverse query)
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Emitter, Finding, find_bsp_root, make_parser, rg_find, require_version,
)

require_version("1.0.0")


def _find_apex_blocks(bsp_root: Path, name: str):
    """Search Android.bp files for `apex { name: "<name>" }` blocks."""
    return rg_find(
        rf'apex\s*\{{[^}}]*name:\s*"{re.escape(name)}"',
        globs=['*.bp'], root=bsp_root,
        extra=['-U', '--multiline-dotall']
    )


def _find_apex_manifest(bsp_root: Path, name: str):
    """Locate apex_manifest.json or .pb for the given APEX."""
    found = []
    for ext in ['json', 'pb']:
        for p in bsp_root.rglob(f'apex_manifest.{ext}'):
            try:
                if ext == 'json':
                    data = json.loads(p.read_text())
                    if data.get('name') == name:
                        found.append((p, data))
                else:
                    # 二进制 .pb，用 strings 兜底
                    if name.encode() in p.read_bytes():
                        found.append((p, None))
            except Exception:
                continue
    return found


def _reverse_lookup_member(bsp_root: Path, member: str):
    """Find which APEX(es) declare `member` in their `native_shared_libs` /
    `binaries` / `prebuilts` lists."""
    return rg_find(
        rf'"{re.escape(member)}"',
        globs=['*.bp'], root=bsp_root,
    )


def main():
    p = make_parser('Locate APEX module definitions / contents.')
    p.add_argument('name',
                   help='APEX module name (com.android.X) or library/binary '
                        'to reverse-lookup which APEX contains it')
    p.add_argument('--reverse', action='store_true',
                   help='treat name as a member to find containing APEX')
    args = p.parse_args()

    try:
        bsp_root = Path(args.root) if args.root else find_bsp_root()
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(2)

    with Emitter(args, Path(__file__).name) as em:
        if args.reverse:
            for hit in _reverse_lookup_member(bsp_root, args.name):
                em.emit(Finding(tag='APEX-CONTAINER', file=hit[0],
                                line=hit[1], snippet=hit[2],
                                info={'member': args.name}),
                        confidence='med', source='static-rg', tags=['apex'])
            return

        # forward lookup: APEX block + manifest + install path
        for hit in _find_apex_blocks(bsp_root, args.name):
            em.emit(Finding(tag='APEX-DEF', file=hit[0], line=hit[1],
                            snippet=hit[2][:200], info={}),
                    confidence='high', source='static-rg', tags=['apex'])

        for path, data in _find_apex_manifest(bsp_root, args.name):
            info = {'version': data.get('version')} if data else {}
            em.emit(Finding(tag='APEX-MANIFEST', file=str(path), line=0,
                            snippet=f'manifest for {args.name}', info=info),
                    confidence='high', source='static-rg', tags=['apex'])

        # install path
        install = bsp_root / 'out' / 'target' / 'product'
        if install.exists():
            for p in install.glob(f'*/system/apex/{args.name}.apex'):
                em.emit(Finding(tag='APEX-INSTALL', file=str(p), line=0,
                                snippet='built apex blob', info={}),
                        confidence='high', source='static-rg', tags=['apex'])


if __name__ == '__main__':
    main()
