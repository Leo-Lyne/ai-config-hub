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


def trace_family(e: Emitter, root: Path, name: str):
    esc = re.escape(name)

    # 1. Kernel: family struct 里 .name = "xxx"
    for f, l, snip in rg_find(rf'\.name\s*=\s*"{esc}"',
                              globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='NL-FAMILY-DEF', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['netlink'])

    # genl_register_family
    for f, l, snip in rg_find(r'genl_register_family\s*\(',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='NL-REGISTER', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['netlink'])

    # 2. Kernel: family ops（在同文件找 .cmd / .doit）
    family_files = set()
    for f, _, _ in rg_find(rf'"{esc}"', globs=['*.c', '*.h'], root=root):
        family_files.add(f)

    for fpath in family_files:
        r = run_cmd(['rg', '-n', r'\.(doit|dumpit|start|done)\s*=\s*\w+', fpath])
        for line in r.stdout.splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m:
                e.emit(Finding(tag='NL-OP', file=fpath, line=int(m.group(1)),
                               snippet=m.group(2).strip()),
                       confidence='med', source='static-rg', tags=['netlink'])

    # genl_ops 数组
    for f, l, snip in rg_find(rf'genl_ops\b.*{esc}|{esc}.*genl_ops',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='NL-OPS-DEF', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['netlink'])

    # 3. Userspace: genl_ctrl_resolve 等
    for f, l, snip in rg_find(rf'genl_ctrl_resolve\s*\([^)]*"{esc}"',
                              globs=['*.c', '*.cpp', '*.h'], root=root):
        e.emit(Finding(tag='NL-USER-RESOLVE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['netlink', 'userspace'])

    # 字符串引用（userspace 库和工具）
    for f, l, snip in rg_find(rf'"{esc}"',
                              globs=['*.c', '*.cpp', '*.h', '*.py', '*.java'],
                              root=root):
        if 'genl' in snip.lower() or 'netlink' in snip.lower() or 'nl_' in snip.lower():
            e.emit(Finding(tag='NL-USER-REF', file=f, line=l, snippet=snip),
                   confidence='low', source='static-rg', tags=['netlink', 'userspace'])

    # NETLINK_* 协议常量（对于非 generic netlink）
    for f, l, snip in rg_find(rf'NETLINK_{esc.upper()}',
                              globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='NL-PROTO', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['netlink'])


def do_scan(e: Emitter, root: Path, out_path: Optional[Path]):
    lines = []

    # 所有 genl_register_family
    for f, l, snip in rg_find(r'genl_register_family\s*\(\s*&?(\w+)',
                              globs=['*.c'], root=root, timeout=300):
        cm = re.search(r'&?(\w+)\s*\)', snip)
        if cm:
            lines.append(f'NL-REGISTER\t{f}:{l}\t{cm.group(1)}')
            e.emit(Finding(tag='NL-REGISTER', file=f, line=l, snippet=cm.group(1)),
                   confidence='med', source='static-rg', tags=['netlink', 'scan'])

    # 所有 .name = "xxx"
    for f, l, snip in rg_find(r'\.name\s*=\s*"(\w+)"',
                              globs=['*.c'], root=root, timeout=300):
        cm = re.search(r'"(\w+)"', snip)
        if cm:
            name = cm.group(1)
            if len(name) >= 3 and not name.startswith('__'):
                lines.append(f'NL-NAME\t{f}:{l}\t{name}')

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('Netlink family 追踪')
    p.add_argument('--family', '-f', help='Netlink family 名（如 nl80211）')
    p.add_argument('--scan', action='store_true', help='全量扫描所有 genl_register_family')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.family:
            trace_family(e, search_root, args.family)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
