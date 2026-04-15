#!/usr/bin/env python3
"""
Android Property 系统追踪：property name 跨进程读写 + init.rc trigger。

用法：
  # 追踪单个 property
  prop_trace.py --property "ro.hardware.chipname"

  # 支持通配（前缀）
  prop_trace.py --property "persist.vendor.camera."

  # 全量扫描所有 property 引用
  prop_trace.py --scan [--out .prop.idx]

识别链路：
  1. Java:    SystemProperties.get/set("prop.name")
  2. Native:  __system_property_get / property_get / android::base::GetProperty
  3. .prop:   build.prop / default.prop / vendor.prop 设初值
  4. SELinux: property_contexts 定义 label
  5. init.rc: on property:prop.name=value → trigger actions
  6. init.rc: setprop / getprop 命令

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


def _rg(root, pattern, globs, timeout=120):
    args = ['rg', '-n', '--no-heading', pattern]
    for g in globs:
        args.extend(['-g', g])
    args.append(str(root))
    results = []
    for line in run(args, timeout).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            results.append((m.group(1), m.group(2), m.group(3).strip()))
    return results


def trace_property(root: Path, prop: str):
    esc = re.escape(prop)

    # 1. Java: SystemProperties.get / set / getInt / getBoolean
    for f, l, c in _rg(root, rf'SystemProperties\.(get|set)\w*\s*\(\s*"{esc}',
                        ['*.java', '*.kt']):
        tag = 'PROP-JAVA-SET' if '.set' in c else 'PROP-JAVA-GET'
        emit(tag, f'{f}:{l}', c)

    # 2. Native C/C++: __system_property_get / property_get / android::base::GetProperty
    for f, l, c in _rg(root, rf'__system_property_(get|set|read)\s*\(\s*"{esc}',
                        ['*.c', '*.cpp', '*.h']):
        emit('PROP-NATIVE', f'{f}:{l}', c)

    for f, l, c in _rg(root, rf'(GetProperty|SetProperty|GetBoolProperty|GetIntProperty)\s*\(\s*"{esc}',
                        ['*.cpp', '*.cc', '*.h']):
        emit('PROP-NATIVE', f'{f}:{l}', c)

    for f, l, c in _rg(root, rf'property_(get|set)\s*\(\s*"{esc}',
                        ['*.c', '*.cpp', '*.h']):
        emit('PROP-NATIVE', f'{f}:{l}', c)

    # 3. .prop 文件：初始值设定
    for f, l, c in _rg(root, rf'^{esc}\s*[=:]',
                        ['*.prop', 'build.prop', 'default.prop', 'vendor_build.prop']):
        emit('PROP-DEFAULT', f'{f}:{l}', c)

    # mk 文件里的 PRODUCT_PROPERTY_OVERRIDES / PRODUCT_SYSTEM_PROPERTIES
    for f, l, c in _rg(root, rf'{esc}\s*[:=]',
                        ['*.mk']):
        if 'PRODUCT_' in c or 'BOARD_' in c or 'property' in c.lower():
            emit('PROP-BUILD', f'{f}:{l}', c)

    # 4. SELinux property_contexts
    for f, l, c in _rg(root, rf'^{esc}',
                        ['*property_contexts*']):
        emit('PROP-SECONTEXT', f'{f}:{l}', c)

    # 5. init.rc: on property:xxx=yyy
    for f, l, c in _rg(root, rf'on\s+property:{esc}',
                        ['*.rc']):
        emit('PROP-RC-TRIGGER', f'{f}:{l}', c)

    # 6. init.rc: setprop / getprop
    for f, l, c in _rg(root, rf'setprop\s+{esc}',
                        ['*.rc']):
        emit('PROP-RC-SET', f'{f}:{l}', c)

    for f, l, c in _rg(root, rf'getprop\s+{esc}',
                        ['*.rc', '*.sh']):
        emit('PROP-RC-GET', f'{f}:{l}', c)


def do_scan(root: Path, out_path: Optional[Path]):
    lines = []

    # 扫所有 SystemProperties.get/set
    for f, l, c in _rg(root, r'SystemProperties\.(get|set)\w*\s*\(\s*"([^"]+)"',
                        ['*.java', '*.kt'], timeout=300):
        cm = re.search(r'"([^"]+)"', c)
        if cm:
            lines.append(f'JAVA-PROP\t{f}:{l}\t{cm.group(1)}')

    # 扫所有 on property: triggers
    for f, l, c in _rg(root, r'on\s+property:([^\s=]+)',
                        ['*.rc'], timeout=300):
        cm = re.search(r'property:([^\s=]+)', c)
        if cm:
            lines.append(f'RC-TRIGGER\t{f}:{l}\t{cm.group(1)}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='Android Property 系统追踪')
    ap.add_argument('--property', '-p', help='property 名（如 "ro.hardware.chipname"）')
    ap.add_argument('--scan', action='store_true', help='全量扫描所有 property 引用')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.property:
        trace_property(args.root, args.property)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
