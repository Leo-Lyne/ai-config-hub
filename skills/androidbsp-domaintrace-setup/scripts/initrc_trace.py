#!/usr/bin/env python3
"""
Init .rc 系统追踪：trigger 链 + service 定义 + USB gadget configfs。

用法：
  # 按 property trigger 追踪：on property:X=Y → actions → services
  initrc_trace.py --trigger "sys.usb.config=mtp"

  # 按 service 名追踪：.rc 定义 → 二进制 → seclabel → 启动条件
  initrc_trace.py --service cameraserver

  # 按 action 名追踪
  initrc_trace.py --action boot

  # USB gadget configfs 追踪（init.rc 里的 configfs 写入 + kernel function driver）
  initrc_trace.py --usb-gadget mtp

  # 全量扫描
  initrc_trace.py --scan [--out .initrc.idx]

识别链路：
  1. on <trigger>       触发条件（boot / property:X=Y / 自定义 action）
  2. start <service>    trigger 内启动的 service
  3. service <name> <binary>   service 定义
  4. seclabel / user / group   安全和权限配置
  5. on property:X=Y → setprop / write / start   触发的动作链

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


def trace_trigger(root: Path, trigger: str):
    """追踪 property trigger 及其 action 链。"""
    esc = re.escape(trigger)

    # 1. on property:xxx=yyy
    for f, l, c in _rg(root, rf'^\s*on\s+property:{esc}',
                        ['*.rc']):
        emit('RC-TRIGGER', f'{f}:{l}', c)
        # 找该 trigger 块内的 actions（从 on 行往下，到下一个 on/service）
        _find_trigger_actions(f, int(l))

    # 2. 也搜不带值的 trigger（on property:xxx=*）
    prop_name = trigger.split('=')[0] if '=' in trigger else trigger
    for f, l, c in _rg(root, rf'on\s+property:{re.escape(prop_name)}',
                        ['*.rc']):
        if trigger not in c:  # 避免和上面重复
            emit('RC-TRIGGER-RELATED', f'{f}:{l}', c)

    # 3. setprop 设置该 property
    for f, l, c in _rg(root, rf'setprop\s+{re.escape(prop_name)}',
                        ['*.rc']):
        emit('RC-SETPROP', f'{f}:{l}', c)


def _find_trigger_actions(fpath: str, trigger_line: int):
    """从 trigger 行开始，找该块内的 start/write/setprop 等 action。"""
    try:
        lines = Path(fpath).read_text().splitlines()
    except OSError:
        return

    for i in range(trigger_line, min(len(lines), trigger_line + 50)):
        line = lines[i].strip()
        # 遇到下一个 on / service 块就停
        if i > trigger_line and re.match(r'^(on|service)\s', line):
            break
        # start <service>
        m = re.match(r'start\s+(\w+)', line)
        if m:
            emit('RC-START', f'{fpath}:{i+1}', line)
        # write / chmod / chown / mkdir
        if re.match(r'(write|chmod|chown|mkdir|symlink|enable|setprop)\s', line):
            emit('RC-ACTION', f'{fpath}:{i+1}', line)


def trace_service(root: Path, name: str):
    """按 service 名追踪完整定义。"""
    esc = re.escape(name)

    # service 定义行
    for f, l, c in _rg(root, rf'^\s*service\s+{esc}\s',
                        ['*.rc']):
        emit('RC-SERVICE', f'{f}:{l}', c)
        # 读取 service 块内的属性
        _find_service_props(f, int(l))

    # 哪些 trigger 会 start 这个 service
    for f, l, c in _rg(root, rf'^\s*start\s+{esc}\s*$',
                        ['*.rc']):
        emit('RC-START-BY', f'{f}:{l}', c)
        # 回溯找 on trigger
        _find_enclosing_trigger(f, int(l))

    # 哪些 trigger 会 stop / restart 这个 service
    for f, l, c in _rg(root, rf'^\s*(stop|restart)\s+{esc}\s*$',
                        ['*.rc']):
        emit('RC-STOP-BY', f'{f}:{l}', c)

    # Android.bp/mk 里对应的二进制定义
    for f, l, c in _rg(root, rf'name\s*:\s*"{esc}"',
                        ['Android.bp']):
        emit('BUILD-DEF', f'{f}:{l}', c)


def _find_service_props(fpath: str, service_line: int):
    """从 service 行开始，提取 seclabel/user/group/class 等属性。"""
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
                emit('RC-PROP', f'{fpath}:{i+1}', line)
                break


def _find_enclosing_trigger(fpath: str, action_line: int):
    """从 action 行回溯找包含它的 on trigger。"""
    try:
        lines = Path(fpath).read_text().splitlines()
    except OSError:
        return

    for i in range(action_line - 1, max(action_line - 50, -1), -1):
        if i < 0:
            break
        line = lines[i].strip()
        if re.match(r'^on\s', line):
            emit('RC-ENCLOSING-TRIGGER', f'{fpath}:{i+1}', line)
            return


def trace_action(root: Path, action: str):
    """追踪 init action（如 boot, late-init）。"""
    esc = re.escape(action)

    for f, l, c in _rg(root, rf'^\s*on\s+{esc}\s*$',
                        ['*.rc']):
        emit('RC-ON-ACTION', f'{f}:{l}', c)
        _find_trigger_actions(f, int(l))

    # trigger <action> 命令
    for f, l, c in _rg(root, rf'^\s*trigger\s+{esc}\s*$',
                        ['*.rc']):
        emit('RC-TRIGGER-CMD', f'{f}:{l}', c)


def trace_usb_gadget(root: Path, function: str):
    """USB gadget configfs 追踪。"""
    esc = re.escape(function)

    # init.rc 里的 configfs 写入
    for f, l, c in _rg(root, rf'(write|symlink)\s+/config/usb_gadget.*{esc}',
                        ['*.rc']):
        emit('USB-RC-CONFIG', f'{f}:{l}', c)

    # sys.usb.config 相关 trigger
    for f, l, c in _rg(root, rf'on\s+property:sys\.usb\.(config|ffs\.ready).*{esc}',
                        ['*.rc']):
        emit('USB-RC-TRIGGER', f'{f}:{l}', c)

    # kernel: DECLARE_USB_FUNCTION / usb_function_register
    for f, l, c in _rg(root, rf'DECLARE_USB_FUNCTION(_INIT)?\s*\(\s*{esc}',
                        ['*.c']):
        emit('USB-KERNEL-FUNC', f'{f}:{l}', c)

    for f, l, c in _rg(root, rf'\.name\s*=\s*"{esc}"',
                        ['*.c']):
        # 限制在 usb 相关文件
        if 'usb' in f.lower() or 'gadget' in f.lower() or 'function' in f.lower():
            emit('USB-KERNEL-NAME', f'{f}:{l}', c)

    # Android.bp/mk 里的 USB 相关模块
    for f, l, c in _rg(root, rf'{esc}',
                        ['*.mk', 'Android.bp']):
        if 'usb' in c.lower() or 'gadget' in c.lower():
            emit('USB-BUILD', f'{f}:{l}', c)


def do_scan(root: Path, out_path: Optional[Path]):
    lines = []

    # 所有 .rc service
    for f, l, c in _rg(root, r'^\s*service\s+(\w+)\s',
                        ['*.rc'], timeout=300):
        cm = re.search(r'service\s+(\w+)', c)
        if cm:
            lines.append(f'RC-SERVICE\t{f}:{l}\t{cm.group(1)}')

    # 所有 on property: triggers
    for f, l, c in _rg(root, r'^\s*on\s+property:(\S+)',
                        ['*.rc'], timeout=300):
        cm = re.search(r'property:(\S+)', c)
        if cm:
            lines.append(f'RC-TRIGGER\t{f}:{l}\t{cm.group(1)}')

    # 所有 on <action> triggers
    for f, l, c in _rg(root, r'^\s*on\s+(boot|init|late-init|early-init|charger|post-fs|post-fs-data)\s*$',
                        ['*.rc'], timeout=300):
        cm = re.search(r'on\s+(\S+)', c)
        if cm:
            lines.append(f'RC-ACTION\t{f}:{l}\t{cm.group(1)}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='Init .rc 系统追踪')
    ap.add_argument('--trigger', '-t', help='property trigger（如 "sys.usb.config=mtp"）')
    ap.add_argument('--service', '-s', help='service 名（如 cameraserver）')
    ap.add_argument('--action', '-a', help='action 名（如 boot）')
    ap.add_argument('--usb-gadget', help='USB gadget function（如 mtp）')
    ap.add_argument('--scan', action='store_true', help='全量扫描')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.trigger:
        trace_trigger(args.root, args.trigger)
    elif args.service:
        trace_service(args.root, args.service)
    elif args.action:
        trace_action(args.root, args.action)
    elif args.usb_gadget:
        trace_usb_gadget(args.root, args.usb_gadget)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
