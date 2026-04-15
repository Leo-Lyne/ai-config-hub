#!/usr/bin/env python3
"""
Netlink family 追踪：kernel genl_register_family ↔ userspace socket 使用。

用法：
  # 按 family 名追踪
  netlink_trace.py --family nl80211

  # 全量扫描所有 generic netlink family 注册
  netlink_trace.py --scan [--out .netlink.idx]

识别链路：
  1. Kernel:   genl_register_family(&xxx_fam)  →  .name = "nl80211"
  2. Kernel:   xxx_fam.ops[]                   →  .cmd / .doit / .dumpit
  3. Userspace: genl_ctrl_resolve / NL_AUTO_PID / NETLINK_GENERIC + family name
  4. Userspace: libnl / iw / wpa_supplicant 等工具对该 family 的使用

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


def trace_family(root: Path, name: str):
    esc = re.escape(name)

    # 1. Kernel: family struct 里 .name = "xxx"
    for f, l, c in _rg(root, rf'\.name\s*=\s*"{esc}"',
                        ['*.c', '*.h']):
        emit('NL-FAMILY-DEF', f'{f}:{l}', c)

    # genl_register_family
    for f, l, c in _rg(root, rf'genl_register_family\s*\(',
                        ['*.c']):
        emit('NL-REGISTER', f'{f}:{l}', c)

    # 2. Kernel: family ops（在同文件找 .cmd / .doit）
    # 先找包含 family name 的文件
    family_files = set()
    for f, l, c in _rg(root, rf'"{esc}"', ['*.c', '*.h']):
        family_files.add(f)

    for fpath in family_files:
        for line in run(['rg', '-n', r'\.(doit|dumpit|start|done)\s*=\s*\w+', fpath]).splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m:
                emit('NL-OP', f'{fpath}:{m.group(1)}', m.group(2).strip())

    # genl_ops 数组
    for f, l, c in _rg(root, rf'genl_ops\b.*{esc}|{esc}.*genl_ops',
                        ['*.c']):
        emit('NL-OPS-DEF', f'{f}:{l}', c)

    # 3. Userspace: genl_ctrl_resolve 等
    for f, l, c in _rg(root, rf'genl_ctrl_resolve\s*\([^)]*"{esc}"',
                        ['*.c', '*.cpp', '*.h']):
        emit('NL-USER-RESOLVE', f'{f}:{l}', c)

    # 字符串引用（userspace 库和工具）
    for f, l, c in _rg(root, rf'"{esc}"',
                        ['*.c', '*.cpp', '*.h', '*.py', '*.java']):
        if 'genl' in c.lower() or 'netlink' in c.lower() or 'nl_' in c.lower():
            emit('NL-USER-REF', f'{f}:{l}', c)

    # NETLINK_* 协议常量（对于非 generic netlink）
    for f, l, c in _rg(root, rf'NETLINK_{esc.upper()}',
                        ['*.c', '*.h']):
        emit('NL-PROTO', f'{f}:{l}', c)


def do_scan(root: Path, out_path: Optional[Path]):
    lines = []

    # 所有 genl_register_family
    for f, l, c in _rg(root, r'genl_register_family\s*\(\s*&?(\w+)',
                        ['*.c'], timeout=300):
        cm = re.search(r'&?(\w+)\s*\)', c)
        if cm:
            lines.append(f'NL-REGISTER\t{f}:{l}\t{cm.group(1)}')

    # 所有 .name = "xxx" 在 genl_family 结构附近
    for f, l, c in _rg(root, r'\.name\s*=\s*"(\w+)"',
                        ['*.c'], timeout=300):
        cm = re.search(r'"(\w+)"', c)
        if cm:
            # 粗筛：只取看起来像 netlink family name 的
            name = cm.group(1)
            if len(name) >= 3 and not name.startswith('__'):
                lines.append(f'NL-NAME\t{f}:{l}\t{name}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='Netlink family 追踪')
    ap.add_argument('--family', '-f', help='Netlink family 名（如 nl80211）')
    ap.add_argument('--scan', action='store_true', help='全量扫描所有 genl_register_family')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.family:
        trace_family(args.root, args.family)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
