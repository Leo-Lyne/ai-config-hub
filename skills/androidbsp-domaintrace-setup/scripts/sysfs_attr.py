#!/usr/bin/env python3
"""
sysfs / procfs / debugfs 属性 ↔ 内核回调追踪。

用法：
  # 按 sysfs 属性名追踪
  sysfs_attr.py --attr brightness

  # 按 procfs 节点名追踪
  sysfs_attr.py --proc interrupts

  # 按 debugfs 节点名追踪
  sysfs_attr.py --debugfs regmap

  # 按回调函数名反查
  sysfs_attr.py --callback brightness_store

  # 全量扫描
  sysfs_attr.py --scan [--out .sysfs_attr.idx]

识别链路扩展（Task 11）：
  sysfs 宏涵盖：DEVICE_ATTR / _RW / _RO / _WO / _ADMIN_RW / _ADMIN_RO、
  BIN_ATTR / _RW / _RO / _WO、STATIC_DEVICE_ATTR、SENSOR_DEVICE_ATTR、
  CLASS_ATTR / BUS_ATTR / DRIVER_ATTR / __ATTR 家族。

依赖：rg。
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Finding, Emitter, make_parser, rg_find, run_cmd, require_version,
)

require_version("1.0.0")


# Task 11：扩展后的 SYSFS 属性宏列表
SYSFS_PATTERNS = [
    # 基础 DEVICE_ATTR 家族
    'DEVICE_ATTR', 'DEVICE_ATTR_RW', 'DEVICE_ATTR_RO', 'DEVICE_ATTR_WO',
    'DEVICE_ATTR_ADMIN_RW', 'DEVICE_ATTR_ADMIN_RO',
    # BIN_ATTR 家族
    'BIN_ATTR', 'BIN_ATTR_RW', 'BIN_ATTR_RO', 'BIN_ATTR_WO',
    # static 变体
    'STATIC_DEVICE_ATTR',
    # class/bus/driver
    'CLASS_ATTR', 'CLASS_ATTR_RW', 'CLASS_ATTR_RO',
    'BUS_ATTR', 'BUS_ATTR_RW', 'BUS_ATTR_RO',
    'DRIVER_ATTR', 'DRIVER_ATTR_RW', 'DRIVER_ATTR_RO',
    # __ATTR
    '__ATTR', '__ATTR_RW', '__ATTR_RO', '__ATTR_WO',
    # sensor
    'SENSOR_DEVICE_ATTR', 'SENSOR_DEVICE_ATTR_RW',
    'SENSOR_DEVICE_ATTR_RO', 'SENSOR_DEVICE_ATTR_WO',
    'SENSOR_DEVICE_ATTR_2', 'SENSOR_DEVICE_ATTR_2_RW',
]


def trace_sysfs_attr(e: Emitter, root: Path, attr_name: str):
    """追踪 sysfs 属性名 → DEVICE_ATTR* 宏 → show/store 回调。"""
    macro_alt = '|'.join(SYSFS_PATTERNS)

    for f, l, snip in rg_find(
            rf'({macro_alt})\s*\(\s*{re.escape(attr_name)}\b',
            globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='ATTR-DEF', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['sysfs', 'attr'])

    # show/store 回调：约定命名 <attr>_show / <attr>_store
    for suffix in ('show', 'store'):
        func_name = f'{attr_name}_{suffix}'
        for f, l, snip in rg_find(
                rf'^\s*static\s+ssize_t\s+{re.escape(func_name)}\s*\(',
                globs=['*.c'], root=root):
            tag = 'SHOW-FUNC' if suffix == 'show' else 'STORE-FUNC'
            e.emit(Finding(tag=tag, file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['sysfs', 'callback'])

    # DEVICE_ATTR 四参数形式
    for f, l, snip in rg_find(
            rf'DEVICE_ATTR\s*\(\s*{re.escape(attr_name)}\s*,\s*\w+\s*,\s*(\w+)\s*,\s*(\w+)\s*\)',
            globs=['*.c', '*.h'], root=root):
        cm = re.search(
            r'DEVICE_ATTR\s*\([^,]+,\s*\w+\s*,\s*(\w+)\s*,\s*(\w+)\s*\)', snip)
        if cm:
            show_fn, store_fn = cm.group(1), cm.group(2)
            if show_fn != 'NULL':
                _find_func_def(e, root, show_fn, 'SHOW-FUNC')
            if store_fn != 'NULL':
                _find_func_def(e, root, store_fn, 'STORE-FUNC')


def trace_proc(e: Emitter, root: Path, node_name: str):
    found_files = set()
    for f, l, snip in rg_find(
            rf'proc_create\w*\s*\([^,]*"{re.escape(node_name)}"',
            globs=['*.c'], root=root):
        e.emit(Finding(tag='PROC-CREATE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['procfs'])
        found_files.add(f)
        cm = re.search(r'&(\w+)\s*\)', snip)
        if cm:
            _find_fops_def(e, cm.group(1), f)

    for f, l, snip in rg_find(
            rf'proc_mkdir\s*\(\s*"{re.escape(node_name)}"',
            globs=['*.c'], root=root):
        e.emit(Finding(tag='PROC-MKDIR', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['procfs'])

    for fpath in found_files:
        r = run_cmd(['rg', '-n', r'(single_open|seq_open)\s*\(', fpath])
        for line in r.stdout.splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m:
                e.emit(Finding(tag='PROC-SEQ', file=fpath, line=int(m.group(1)),
                               snippet=m.group(2).strip()),
                       confidence='med', source='static-rg', tags=['procfs'])


def trace_debugfs(e: Emitter, root: Path, node_name: str):
    for f, l, snip in rg_find(
            rf'debugfs_create_file\s*\(\s*"{re.escape(node_name)}"',
            globs=['*.c'], root=root):
        e.emit(Finding(tag='DEBUGFS-CREATE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['debugfs'])
        cm = re.search(r'&(\w+)\s*\)', snip)
        if cm:
            _find_fops_def(e, cm.group(1), f)

    for f, l, snip in rg_find(
            rf'debugfs_create_dir\s*\(\s*"{re.escape(node_name)}"',
            globs=['*.c'], root=root):
        e.emit(Finding(tag='DEBUGFS-DIR', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['debugfs'])


def trace_callback(e: Emitter, root: Path, func_name: str):
    _find_func_def(e, root, func_name, 'CALLBACK-DEF')

    macro_alt = '|'.join(
        ['DEVICE_ATTR', 'CLASS_ATTR', 'BUS_ATTR', 'DRIVER_ATTR', '__ATTR',
         'BIN_ATTR', 'STATIC_DEVICE_ATTR', 'SENSOR_DEVICE_ATTR'])
    for f, l, snip in rg_find(
            rf'({macro_alt})\w*\s*\([^)]*\b{re.escape(func_name)}\b',
            globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='ATTR-BIND', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['sysfs', 'attr'])

    for f, l, snip in rg_find(
            rf'\.(read|write|open|show|store)\s*=\s*{re.escape(func_name)}\b',
            globs=['*.c'], root=root):
        e.emit(Finding(tag='FOPS-BIND', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['fops'])


def _find_func_def(e: Emitter, root: Path, func_name: str, tag: str):
    for f, l, snip in rg_find(
            rf'^\s*(?:static\s+)?(?:ssize_t|int|void|long)\s+{re.escape(func_name)}\s*\(',
            globs=['*.c'], root=root):
        e.emit(Finding(tag=tag, file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['sysfs', 'callback'])


def _find_fops_def(e: Emitter, fops_name: str, hint_file: str):
    r = run_cmd(['rg', '-n',
                 rf'(struct\s+(file_operations|proc_ops)\s+{re.escape(fops_name)}\b'
                 rf'|\.read\s*=|\.write\s*=|\.open\s*=|\.release\s*=)',
                 hint_file])
    for line in r.stdout.splitlines():
        m = re.match(r'^(\d+):(.*)$', line)
        if m:
            e.emit(Finding(tag='FOPS-DEF', file=hint_file,
                           line=int(m.group(1)), snippet=m.group(2).strip()),
                   confidence='med', source='static-rg', tags=['fops'])


def do_scan(e: Emitter, root: Path, out_path: Optional[Path]):
    lines = []
    macro_alt = '|'.join(SYSFS_PATTERNS)
    for f, l, snip in rg_find(
            rf'({macro_alt})\s*\(\s*(\w+)',
            globs=['*.c', '*.h'], root=root, timeout=300):
        cm = re.search(rf'({macro_alt})\s*\(\s*(\w+)', snip)
        if cm:
            lines.append(f'{cm.group(1)}\t{f}:{l}\t{cm.group(2)}')
            e.emit(Finding(tag=cm.group(1), file=f, line=l, snippet=cm.group(2)),
                   confidence='high', source='static-rg', tags=['sysfs', 'scan'])

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('sysfs/procfs/debugfs 属性 ↔ 内核回调追踪')
    p.add_argument('--attr', '-a', help='sysfs 属性名（如 brightness）')
    p.add_argument('--proc', help='procfs 节点名（如 interrupts）')
    p.add_argument('--debugfs', help='debugfs 节点名（如 regmap）')
    p.add_argument('--callback', help='回调函数名，反查绑定的节点')
    p.add_argument('--scan', action='store_true',
                   help='全量扫描所有 DEVICE_ATTR* 定义')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.attr:
            trace_sysfs_attr(e, search_root, args.attr)
        elif args.proc:
            trace_proc(e, search_root, args.proc)
        elif args.debugfs:
            trace_debugfs(e, search_root, args.debugfs)
        elif args.callback:
            trace_callback(e, search_root, args.callback)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
