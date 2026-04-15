#!/usr/bin/env python3
"""
Init .rc 系统追踪：trigger 链 + service 定义 + USB gadget configfs。

用法：
  initrc_trace.py --trigger "sys.usb.config=mtp"
  initrc_trace.py --service cameraserver
  initrc_trace.py --action boot
  initrc_trace.py --usb-gadget mtp
  initrc_trace.py --scan [--out .initrc.idx]

Task 11 扩展：6 种 init.rc 来源
  - system/etc/init
  - vendor/etc/init
  - odm/etc/init
  - system_ext/etc/init
  - product/etc/init
  - apex/*/etc  (APEX 模块)

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


def _init_rc_roots(bsp_root: Path) -> list[Path]:
    """返回所有存在的 init.rc 搜索根。"""
    roots: list[Path] = []
    # 5 个分区下的 etc/init
    for part_init in scan_partitions(bsp_root, 'etc/init'):
        roots.append(part_init)
    # APEX 里的 etc
    apex_dir = bsp_root / 'apex'
    if apex_dir.is_dir():
        for p in sorted(apex_dir.glob('*/etc')):
            if p.is_dir():
                roots.append(p)
    # 如果上面都没探测到，退化到整个 bsp_root（兼容 source tree）
    if not roots:
        roots.append(bsp_root)
    return roots


def trace_trigger(e: Emitter, bsp_root: Path, trigger: str):
    esc = re.escape(trigger)

    for root in _init_rc_roots(bsp_root):
        for f, l, snip in rg_find(rf'^\s*on\s+property:{esc}',
                                  globs=['*.rc'], root=root):
            e.emit(Finding(tag='RC-TRIGGER', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['init', 'trigger'])
            _find_trigger_actions(e, f, int(l))

        prop_name = trigger.split('=')[0] if '=' in trigger else trigger
        for f, l, snip in rg_find(
                rf'on\s+property:{re.escape(prop_name)}',
                globs=['*.rc'], root=root):
            if trigger not in snip:
                e.emit(Finding(tag='RC-TRIGGER-RELATED', file=f, line=l,
                               snippet=snip),
                       confidence='med', source='static-rg',
                       tags=['init', 'trigger'])

        for f, l, snip in rg_find(
                rf'setprop\s+{re.escape(prop_name)}',
                globs=['*.rc'], root=root):
            e.emit(Finding(tag='RC-SETPROP', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['init', 'setprop'])


def _find_trigger_actions(e: Emitter, fpath: str, trigger_line: int):
    try:
        lines = Path(fpath).read_text().splitlines()
    except OSError:
        return
    for i in range(trigger_line, min(len(lines), trigger_line + 50)):
        line = lines[i].strip()
        if i > trigger_line and re.match(r'^(on|service)\s', line):
            break
        m = re.match(r'start\s+(\w+)', line)
        if m:
            e.emit(Finding(tag='RC-START', file=fpath, line=i + 1, snippet=line),
                   confidence='high', source='static-rg',
                   tags=['init', 'action'])
        if re.match(r'(write|chmod|chown|mkdir|symlink|enable|setprop)\s', line):
            e.emit(Finding(tag='RC-ACTION', file=fpath, line=i + 1, snippet=line),
                   confidence='med', source='static-rg',
                   tags=['init', 'action'])


def trace_service(e: Emitter, bsp_root: Path, name: str):
    esc = re.escape(name)

    for root in _init_rc_roots(bsp_root):
        for f, l, snip in rg_find(rf'^\s*service\s+{esc}\s',
                                  globs=['*.rc'], root=root):
            e.emit(Finding(tag='RC-SERVICE', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['init', 'service'])
            _find_service_props(e, f, int(l))

        for f, l, snip in rg_find(rf'^\s*start\s+{esc}\s*$',
                                  globs=['*.rc'], root=root):
            e.emit(Finding(tag='RC-START-BY', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['init', 'service'])
            _find_enclosing_trigger(e, f, int(l))

        for f, l, snip in rg_find(rf'^\s*(stop|restart)\s+{esc}\s*$',
                                  globs=['*.rc'], root=root):
            e.emit(Finding(tag='RC-STOP-BY', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['init', 'service'])

    # Android.bp 源码定义
    for f, l, snip in rg_find(rf'name\s*:\s*"{esc}"',
                              globs=['Android.bp'], root=bsp_root):
        e.emit(Finding(tag='BUILD-DEF', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg',
               tags=['init', 'build'])


def _find_service_props(e: Emitter, fpath: str, service_line: int):
    try:
        lines = Path(fpath).read_text().splitlines()
    except OSError:
        return
    for i in range(service_line, min(len(lines), service_line + 30)):
        line = lines[i].strip()
        if i > service_line and re.match(r'^(on|service)\s', line):
            break
        for kw in ('seclabel', 'user', 'group', 'class', 'interface',
                   'disabled', 'oneshot', 'writepid', 'capabilities'):
            if re.match(rf'{kw}\s', line):
                e.emit(Finding(tag='RC-PROP', file=fpath, line=i + 1,
                               snippet=line),
                       confidence='med', source='static-rg',
                       tags=['init', 'service'])
                break


def _find_enclosing_trigger(e: Emitter, fpath: str, action_line: int):
    try:
        lines = Path(fpath).read_text().splitlines()
    except OSError:
        return
    for i in range(action_line - 1, max(action_line - 50, -1), -1):
        if i < 0:
            break
        line = lines[i].strip()
        if re.match(r'^on\s', line):
            e.emit(Finding(tag='RC-ENCLOSING-TRIGGER', file=fpath,
                           line=i + 1, snippet=line),
                   confidence='med', source='static-rg',
                   tags=['init', 'trigger'])
            return


def trace_action(e: Emitter, bsp_root: Path, action: str):
    esc = re.escape(action)

    for root in _init_rc_roots(bsp_root):
        for f, l, snip in rg_find(rf'^\s*on\s+{esc}\s*$',
                                  globs=['*.rc'], root=root):
            e.emit(Finding(tag='RC-ON-ACTION', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['init', 'action'])
            _find_trigger_actions(e, f, int(l))

        for f, l, snip in rg_find(rf'^\s*trigger\s+{esc}\s*$',
                                  globs=['*.rc'], root=root):
            e.emit(Finding(tag='RC-TRIGGER-CMD', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['init', 'action'])


def trace_usb_gadget(e: Emitter, bsp_root: Path, function: str):
    esc = re.escape(function)

    for root in _init_rc_roots(bsp_root):
        for f, l, snip in rg_find(
                rf'(write|symlink)\s+/config/usb_gadget.*{esc}',
                globs=['*.rc'], root=root):
            e.emit(Finding(tag='USB-RC-CONFIG', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['usb', 'gadget'])

        for f, l, snip in rg_find(
                rf'on\s+property:sys\.usb\.(config|ffs\.ready).*{esc}',
                globs=['*.rc'], root=root):
            e.emit(Finding(tag='USB-RC-TRIGGER', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['usb', 'gadget'])

    for f, l, snip in rg_find(
            rf'DECLARE_USB_FUNCTION(_INIT)?\s*\(\s*{esc}',
            globs=['*.c'], root=bsp_root):
        e.emit(Finding(tag='USB-KERNEL-FUNC', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg',
               tags=['usb', 'kernel'])

    for f, l, snip in rg_find(rf'\.name\s*=\s*"{esc}"',
                              globs=['*.c'], root=bsp_root):
        if 'usb' in f.lower() or 'gadget' in f.lower() or 'function' in f.lower():
            e.emit(Finding(tag='USB-KERNEL-NAME', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg',
                   tags=['usb', 'kernel'])

    for f, l, snip in rg_find(rf'{esc}',
                              globs=['*.mk', 'Android.bp'], root=bsp_root):
        if 'usb' in snip.lower() or 'gadget' in snip.lower():
            e.emit(Finding(tag='USB-BUILD', file=f, line=l, snippet=snip),
                   confidence='low', source='static-rg',
                   tags=['usb', 'build'])


def do_scan(e: Emitter, bsp_root: Path, out_path: Optional[Path]):
    lines = []

    for root in _init_rc_roots(bsp_root):
        for f, l, snip in rg_find(r'^\s*service\s+(\w+)\s',
                                  globs=['*.rc'], root=root, timeout=300):
            cm = re.search(r'service\s+(\w+)', snip)
            if cm:
                lines.append(f'RC-SERVICE\t{f}:{l}\t{cm.group(1)}')

        for f, l, snip in rg_find(r'^\s*on\s+property:(\S+)',
                                  globs=['*.rc'], root=root, timeout=300):
            cm = re.search(r'property:(\S+)', snip)
            if cm:
                lines.append(f'RC-TRIGGER\t{f}:{l}\t{cm.group(1)}')

        for f, l, snip in rg_find(
                r'^\s*on\s+(boot|init|late-init|early-init|charger|post-fs|post-fs-data)\s*$',
                globs=['*.rc'], root=root, timeout=300):
            cm = re.search(r'on\s+(\S+)', snip)
            if cm:
                lines.append(f'RC-ACTION\t{f}:{l}\t{cm.group(1)}')

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('Init .rc 系统追踪（多来源）')
    p.add_argument('--trigger', '-t', help='property trigger')
    p.add_argument('--service', '-s', help='service 名')
    p.add_argument('--action', '-a', help='action 名')
    p.add_argument('--usb-gadget', dest='usb_gadget', help='USB gadget function')
    p.add_argument('--scan', action='store_true', help='全量扫描')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.trigger:
            trace_trigger(e, search_root, args.trigger)
        elif args.service:
            trace_service(e, search_root, args.service)
        elif args.action:
            trace_action(e, search_root, args.action)
        elif args.usb_gadget:
            trace_usb_gadget(e, search_root, args.usb_gadget)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
