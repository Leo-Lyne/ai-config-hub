#!/usr/bin/env python3
"""
Android Property 系统追踪：property name 跨进程读写 + init.rc trigger。

用法：
  prop_trace.py --property "ro.hardware.chipname"
  prop_trace.py --property "persist.vendor.camera."
  prop_trace.py --scan [--out .prop.idx]

Task 11 扩展：多分区 build.prop 探测
  扫描 system / vendor / odm / system_ext / product 各分区的 build.prop /
  default.prop / <part>_build.prop，同时兼容旧的扁平路径。

依赖：rg。
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Finding, Emitter, make_parser, rg_find, scan_partitions, require_version,
)

require_version("1.0.0")


def _prop_file_candidates(bsp_root: Path) -> list[Path]:
    """多分区 build.prop / default.prop / <part>_build.prop。"""
    files: list[Path] = []
    # 多分区下 etc/build.prop、build.prop、default.prop
    for part_dir in scan_partitions(bsp_root, ''):
        for sub in ('build.prop', 'etc/build.prop', 'default.prop',
                    'etc/default.prop'):
            cand = part_dir / sub
            if cand.exists():
                files.append(cand)
    # 兼容顶层扁平路径
    for name in ('build.prop', 'default.prop', 'vendor_build.prop'):
        cand = bsp_root / name
        if cand.exists():
            files.append(cand)
    return files


def trace_property(e: Emitter, bsp_root: Path, prop: str):
    esc = re.escape(prop)

    # 1. Java
    for f, l, snip in rg_find(
            rf'SystemProperties\.(get|set)\w*\s*\(\s*"{esc}',
            globs=['*.java', '*.kt'], root=bsp_root):
        tag = 'PROP-JAVA-SET' if '.set' in snip else 'PROP-JAVA-GET'
        e.emit(Finding(tag=tag, file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['property', 'java'])

    # 2. Native
    for f, l, snip in rg_find(
            rf'__system_property_(get|set|read)\s*\(\s*"{esc}',
            globs=['*.c', '*.cpp', '*.h'], root=bsp_root):
        e.emit(Finding(tag='PROP-NATIVE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['property', 'native'])

    for f, l, snip in rg_find(
            rf'(GetProperty|SetProperty|GetBoolProperty|GetIntProperty)\s*\(\s*"{esc}',
            globs=['*.cpp', '*.cc', '*.h'], root=bsp_root):
        e.emit(Finding(tag='PROP-NATIVE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['property', 'native'])

    for f, l, snip in rg_find(
            rf'property_(get|set)\s*\(\s*"{esc}',
            globs=['*.c', '*.cpp', '*.h'], root=bsp_root):
        e.emit(Finding(tag='PROP-NATIVE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['property', 'native'])

    # 3. build.prop：多分区扫描
    prop_files = _prop_file_candidates(bsp_root)
    for pf in prop_files:
        try:
            for idx, line in enumerate(pf.read_text(errors='ignore').splitlines(), 1):
                if re.match(rf'^{esc}\s*[=:]', line):
                    e.emit(Finding(tag='PROP-DEFAULT', file=str(pf),
                                   line=idx, snippet=line.strip()),
                           confidence='high', source='static-rg',
                           tags=['property', 'build.prop'])
        except OSError:
            continue

    # 兜底：rg 直接搜常见 *.prop
    for f, l, snip in rg_find(rf'^{esc}\s*[=:]',
                              globs=['*.prop', 'build.prop', 'default.prop',
                                     'vendor_build.prop'],
                              root=bsp_root):
        e.emit(Finding(tag='PROP-DEFAULT', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg',
               tags=['property', 'build.prop'])

    # mk
    for f, l, snip in rg_find(rf'{esc}\s*[:=]',
                              globs=['*.mk'], root=bsp_root):
        if 'PRODUCT_' in snip or 'BOARD_' in snip or 'property' in snip.lower():
            e.emit(Finding(tag='PROP-BUILD', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg',
                   tags=['property', 'build'])

    # 4. SELinux property_contexts
    for f, l, snip in rg_find(rf'^{esc}',
                              globs=['*property_contexts*'], root=bsp_root):
        e.emit(Finding(tag='PROP-SECONTEXT', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg',
               tags=['property', 'selinux'])

    # 5. init.rc
    for f, l, snip in rg_find(rf'on\s+property:{esc}',
                              globs=['*.rc'], root=bsp_root):
        e.emit(Finding(tag='PROP-RC-TRIGGER', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['property', 'init'])

    for f, l, snip in rg_find(rf'setprop\s+{esc}',
                              globs=['*.rc'], root=bsp_root):
        e.emit(Finding(tag='PROP-RC-SET', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['property', 'init'])

    for f, l, snip in rg_find(rf'getprop\s+{esc}',
                              globs=['*.rc', '*.sh'], root=bsp_root):
        e.emit(Finding(tag='PROP-RC-GET', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['property', 'init'])


def do_scan(e: Emitter, root: Path, out_path: Optional[Path]):
    lines = []

    for f, l, snip in rg_find(
            r'SystemProperties\.(get|set)\w*\s*\(\s*"([^"]+)"',
            globs=['*.java', '*.kt'], root=root, timeout=300):
        cm = re.search(r'"([^"]+)"', snip)
        if cm:
            lines.append(f'JAVA-PROP\t{f}:{l}\t{cm.group(1)}')

    for f, l, snip in rg_find(r'on\s+property:([^\s=]+)',
                              globs=['*.rc'], root=root, timeout=300):
        cm = re.search(r'property:([^\s=]+)', snip)
        if cm:
            lines.append(f'RC-TRIGGER\t{f}:{l}\t{cm.group(1)}')

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('Android Property 系统追踪（多分区）')
    p.add_argument('--property', '-p', help='property 名')
    p.add_argument('--scan', action='store_true', help='全量扫描')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.property:
            trace_property(e, search_root, args.property)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
