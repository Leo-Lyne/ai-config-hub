#!/usr/bin/env python3
"""
sysfs / procfs / debugfs 属性 ↔ 内核回调追踪。

用法：
  # 按 sysfs 属性名追踪（找 DEVICE_ATTR* 宏 → show/store 回调）
  sysfs_attr.py --attr brightness

  # 按 procfs 节点名追踪（找 proc_create → proc_ops / fops 回调）
  sysfs_attr.py --proc interrupts

  # 按 debugfs 节点名追踪
  sysfs_attr.py --debugfs regmap

  # 按回调函数名反查：哪个 sysfs/procfs 节点绑定了它
  sysfs_attr.py --callback brightness_store

  # 全量扫描：列出所有 DEVICE_ATTR* 定义
  sysfs_attr.py --scan [--out .sysfs_attr.idx]

识别链路：
  sysfs:
    1. DEVICE_ATTR / DEVICE_ATTR_RW / DEVICE_ATTR_RO / DEVICE_ATTR_WO
    2. CLASS_ATTR / CLASS_ATTR_RW / BUS_ATTR / DRIVER_ATTR
    3. __ATTR / __ATTR_RW / __ATTR_RO / __ATTR_WO
    4. sysfs_create_file / sysfs_create_group
    → show / store 回调函数

  procfs:
    1. proc_create / proc_create_data / proc_mkdir
    2. proc_ops / file_operations (.read / .write / .open)

  debugfs:
    1. debugfs_create_file / debugfs_create_dir
    2. file_operations (.read / .write / .open)

依赖：rg。
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


def run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ''


def emit(tag: str, location: str, info: str = ''):
    print(f'{tag}\t{location}\t{info}')


def trace_sysfs_attr(root: Path, attr_name: str):
    """追踪 sysfs 属性名 → DEVICE_ATTR* 宏 → show/store 回调。"""

    # DEVICE_ATTR(name, mode, show, store)
    # DEVICE_ATTR_RW(name) — 自动推导 name_show / name_store
    # DEVICE_ATTR_RO(name) — name_show
    # DEVICE_ATTR_WO(name) — name_store
    attr_macros = [
        'DEVICE_ATTR', 'DEVICE_ATTR_RW', 'DEVICE_ATTR_RO', 'DEVICE_ATTR_WO',
        'CLASS_ATTR', 'CLASS_ATTR_RW', 'CLASS_ATTR_RO',
        'BUS_ATTR', 'BUS_ATTR_RW', 'BUS_ATTR_RO',
        'DRIVER_ATTR', 'DRIVER_ATTR_RW', 'DRIVER_ATTR_RO',
        '__ATTR', '__ATTR_RW', '__ATTR_RO', '__ATTR_WO',
        'SENSOR_DEVICE_ATTR', 'SENSOR_DEVICE_ATTR_RW',
        'SENSOR_DEVICE_ATTR_RO', 'SENSOR_DEVICE_ATTR_WO',
        'SENSOR_DEVICE_ATTR_2', 'SENSOR_DEVICE_ATTR_2_RW',
    ]
    macro_alt = '|'.join(attr_macros)

    args = ['rg', '-n', '--no-heading',
            rf'({macro_alt})\s*\(\s*{re.escape(attr_name)}\b',
            '-g', '*.c', '-g', '*.h', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('ATTR-DEF', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 找 show/store 回调：约定命名 <attr>_show / <attr>_store
    for suffix in ('show', 'store'):
        func_name = f'{attr_name}_{suffix}'
        args = ['rg', '-n', '--no-heading',
                rf'^\s*static\s+ssize_t\s+{re.escape(func_name)}\s*\(',
                '-g', '*.c', str(root)]
        for line in run(args).splitlines():
            m = re.match(r'^([^:]+):(\d+):(.*)$', line)
            if m:
                tag = 'SHOW-FUNC' if suffix == 'show' else 'STORE-FUNC'
                emit(tag, f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 也搜 DEVICE_ATTR 四参数形式里直接指定的非标准回调名
    args = ['rg', '-n', '--no-heading',
            rf'DEVICE_ATTR\s*\(\s*{re.escape(attr_name)}\s*,\s*\w+\s*,\s*(\w+)\s*,\s*(\w+)\s*\)',
            '-g', '*.c', '-g', '*.h', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            snippet = m.group(3)
            cm = re.search(r'DEVICE_ATTR\s*\([^,]+,\s*\w+\s*,\s*(\w+)\s*,\s*(\w+)\s*\)', snippet)
            if cm:
                show_fn, store_fn = cm.group(1), cm.group(2)
                if show_fn != 'NULL':
                    _find_func_def(root, show_fn, 'SHOW-FUNC')
                if store_fn != 'NULL':
                    _find_func_def(root, store_fn, 'STORE-FUNC')


def trace_proc(root: Path, node_name: str):
    """追踪 procfs 节点名 → proc_create* → proc_ops / fops 回调。"""
    # proc_create("name", ..., &fops)
    args = ['rg', '-n', '--no-heading',
            rf'proc_create\w*\s*\([^,]*"{re.escape(node_name)}"',
            '-g', '*.c', str(root)]
    found_files = set()
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('PROC-CREATE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())
            found_files.add(m.group(1))
            # 提取 fops 变量名
            cm = re.search(r'&(\w+)\s*\)', m.group(3))
            if cm:
                _find_fops_def(root, cm.group(1), m.group(1))

    # proc_mkdir
    args = ['rg', '-n', '--no-heading',
            rf'proc_mkdir\s*\(\s*"{re.escape(node_name)}"',
            '-g', '*.c', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('PROC-MKDIR', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 也找 single_open / seq_file 风格
    for fpath in found_files:
        for line in run(['rg', '-n', r'(single_open|seq_open)\s*\(', fpath]).splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m:
                emit('PROC-SEQ', f'{fpath}:{m.group(1)}', m.group(2).strip())


def trace_debugfs(root: Path, node_name: str):
    """追踪 debugfs 节点名 → debugfs_create_file → fops 回调。"""
    args = ['rg', '-n', '--no-heading',
            rf'debugfs_create_file\s*\(\s*"{re.escape(node_name)}"',
            '-g', '*.c', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('DEBUGFS-CREATE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())
            cm = re.search(r'&(\w+)\s*\)', m.group(3))
            if cm:
                _find_fops_def(root, cm.group(1), m.group(1))

    # debugfs_create_dir
    args = ['rg', '-n', '--no-heading',
            rf'debugfs_create_dir\s*\(\s*"{re.escape(node_name)}"',
            '-g', '*.c', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('DEBUGFS-DIR', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def trace_callback(root: Path, func_name: str):
    """从回调函数名反查绑定的 sysfs/procfs/debugfs 节点。"""
    # 函数定义
    _find_func_def(root, func_name, 'CALLBACK-DEF')

    # 被哪个 DEVICE_ATTR* 引用
    args = ['rg', '-n', '--no-heading',
            rf'(DEVICE_ATTR|CLASS_ATTR|BUS_ATTR|DRIVER_ATTR|__ATTR)\w*\s*\([^)]*\b{re.escape(func_name)}\b',
            '-g', '*.c', '-g', '*.h', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('ATTR-BIND', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 被哪个 file_operations / proc_ops 引用
    args = ['rg', '-n', '--no-heading',
            rf'\.(read|write|open|show|store)\s*=\s*{re.escape(func_name)}\b',
            '-g', '*.c', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('FOPS-BIND', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def _find_func_def(root: Path, func_name: str, tag: str):
    """找 C 函数定义。"""
    args = ['rg', '-n', '--no-heading',
            rf'^\s*(?:static\s+)?(?:ssize_t|int|void|long)\s+{re.escape(func_name)}\s*\(',
            '-g', '*.c', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit(tag, f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def _find_fops_def(root: Path, fops_name: str, hint_file: str):
    """找 file_operations / proc_ops struct 定义并列出回调成员。"""
    # 优先在同文件里找
    for line in run(['rg', '-n',
                     rf'(struct\s+(file_operations|proc_ops)\s+{re.escape(fops_name)}\b'
                     rf'|\.read\s*=|\.write\s*=|\.open\s*=|\.release\s*=)',
                     hint_file]).splitlines():
        m = re.match(r'^(\d+):(.*)$', line)
        if m:
            emit('FOPS-DEF', f'{hint_file}:{m.group(1)}', m.group(2).strip())


def do_scan(root: Path, out_path: Optional[Path]):
    """全量扫描所有 DEVICE_ATTR* 定义。"""
    lines = []
    args = ['rg', '-n', '--no-heading',
            r'(DEVICE_ATTR|CLASS_ATTR|BUS_ATTR|DRIVER_ATTR)\w*\s*\(\s*(\w+)',
            '-g', '*.c', '-g', '*.h', str(root)]
    for line in run(args, timeout=300).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            cm = re.search(r'(DEVICE_ATTR|CLASS_ATTR|BUS_ATTR|DRIVER_ATTR)\w*\s*\(\s*(\w+)', m.group(3))
            if cm:
                lines.append(f'{cm.group(1)}\t{m.group(1)}:{m.group(2)}\t{cm.group(2)}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='sysfs/procfs/debugfs 属性 ↔ 内核回调追踪')
    ap.add_argument('--attr', '-a', help='sysfs 属性名（如 brightness）')
    ap.add_argument('--proc', help='procfs 节点名（如 interrupts）')
    ap.add_argument('--debugfs', help='debugfs 节点名（如 regmap）')
    ap.add_argument('--callback', help='回调函数名，反查绑定的节点')
    ap.add_argument('--scan', action='store_true', help='全量扫描所有 DEVICE_ATTR* 定义')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.attr:
        trace_sysfs_attr(args.root, args.attr)
    elif args.proc:
        trace_proc(args.root, args.proc)
    elif args.debugfs:
        trace_debugfs(args.root, args.debugfs)
    elif args.callback:
        trace_callback(args.root, args.callback)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
