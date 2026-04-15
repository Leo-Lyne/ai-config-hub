#!/usr/bin/env python3
"""
syscall 跨边界追踪：userspace 符号 -> bionic wrapper -> syscall 号 -> kernel 入口 -> vfs。

用法：
  # 按名字追
  syscall_trace.py openat
  syscall_trace.py --name openat

  # 按号追（十进制或 0x 开头）
  syscall_trace.py --nr 56
  syscall_trace.py --nr 0x38

  # 强制 arch
  syscall_trace.py openat --arch arm64

识别链路（arm / arm64）：
  1. bionic/libc wrapper        bionic/libc/**/SYS_*.S  或 libc/include/**/syscall.h
  2. syscall 号                  kernel/.../arch/arm64/include/asm/unistd*.h
                                 或 kernel/.../include/uapi/asm-generic/unistd.h
  3. SYSCALL_DEFINE 入口        fs/open.c 等：SYSCALL_DEFINE3(openat, ...)
  4. ksys_* / do_sys_*          内部辅助，通常同文件
  5. vfs_*                       vfs 层调用

kernel 根目录自动探测：
  - 优先读 compile_commands.json 里出现的 arch/arm64/ 或 arch/arm/ 路径
  - 回退：BSP 根下找 kernel/、kernel-*/、bsp/kernel*/ 等常见目录
"""

from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Emitter, Finding, make_parser, require_version, run_cmd,
)

require_version("1.0.0")


def find_kernel_root(bsp_root: Path, arch: str) -> Optional[Path]:
    """
    优先从 compile_commands.json 解析出 kernel 源根。
    回退：按常见命名猜测。
    """
    ccj = bsp_root / 'compile_commands.json'
    if ccj.exists():
        try:
            data = json.loads(ccj.read_text())
            marker = f'/arch/{arch}/'
            for entry in data:
                f = entry.get('file', '')
                d = entry.get('directory', '')
                for s in (f, d):
                    idx = s.find(marker)
                    if idx > 0:
                        kroot = Path(s[:idx])
                        if (kroot / 'arch' / arch).is_dir():
                            return kroot
        except (json.JSONDecodeError, OSError):
            pass

    candidates = [
        bsp_root / 'kernel',
        bsp_root / 'kernel_platform' / 'msm-kernel',
        bsp_root / 'kernel_platform' / 'common',
    ]
    for p in bsp_root.glob('kernel-*'):
        if p.is_dir():
            candidates.append(p)
    for p in bsp_root.glob('kernel/msm-*'):
        candidates.append(p)
    for p in bsp_root.glob('bsp/kernel*'):
        candidates.append(p)

    for c in candidates:
        if (c / 'arch' / arch).is_dir() and (c / 'include').is_dir():
            return c
    return None


def _rg_lines(pattern: str, globs: list[str], root: Path, timeout: int):
    cmd = ['rg', '-n', '--no-heading']
    for g in globs:
        cmd += ['-g', g]
    cmd += [pattern, str(root)]
    r = run_cmd(cmd, timeout=timeout)
    out = []
    for line in r.stdout.splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            out.append((m.group(1), int(m.group(2)), m.group(3).strip()))
    return out


def find_bionic_wrapper(e: Emitter, bsp_root: Path, name: str, timeout: int):
    """bionic syscall wrapper: .S 文件 + SYSCALLS.TXT"""
    scan_root = bsp_root / 'bionic' if (bsp_root / 'bionic').exists() else bsp_root
    r = run_cmd(['fd', '--type', 'f', f'^{re.escape(name)}\\.S$', str(scan_root)],
                timeout=timeout)
    for s in r.stdout.splitlines():
        if s.strip():
            e.emit(Finding(tag='USER-WRAPPER', file=s.strip(), line=0,
                           snippet=f'bionic .S stub for {name}'),
                   confidence='med', source='static-rg', tags=['syscall', 'bionic'])

    for fpath, line_no, snip in _rg_lines(
            rf'\b{re.escape(name)}\b', ['SYSCALLS.TXT'], bsp_root, timeout):
        e.emit(Finding(tag='USER-WRAPPER', file=fpath, line=line_no, snippet=snip),
               confidence='med', source='static-rg', tags=['syscall', 'bionic'])


def find_syscall_number(e: Emitter, kroot: Path, arch: str, name: str,
                        timeout: int):
    """syscall 号来源 (arm / arm64)."""
    if arch == 'arm64':
        globs = [
            'arch/arm64/include/**/unistd*.h',
            'include/uapi/asm-generic/unistd.h',
        ]
    else:
        globs = [
            'arch/arm/include/**/unistd*.h',
            'arch/arm/tools/syscall.tbl',
            'include/uapi/asm-generic/unistd.h',
        ]

    for g in globs:
        for fpath, line_no, snip in _rg_lines(
                rf'\b__NR_{re.escape(name)}\b', [g], kroot, timeout):
            e.emit(Finding(tag='SYSCALL-NR', file=fpath, line=line_no, snippet=snip),
                   confidence='high', source='static-rg', tags=['syscall', 'nr'])

    if arch == 'arm':
        tbl = kroot / 'arch' / 'arm' / 'tools' / 'syscall.tbl'
        if tbl.exists():
            for i, ln in enumerate(tbl.read_text().splitlines(), 1):
                parts = ln.split()
                if len(parts) >= 4 and parts[3] == f'sys_{name}':
                    e.emit(Finding(tag='SYSCALL-NR', file=str(tbl), line=i,
                                   snippet=ln.strip()),
                           confidence='high', source='static-rg',
                           tags=['syscall', 'nr'])


def find_syscall_by_nr(e: Emitter, kroot: Path, nr: int, timeout: int):
    """从号反查名字。"""
    nr_dec = str(nr)
    nr_hex = hex(nr)
    cmd = ['rg', '-n', rf'#define\s+__NR_\w+\s+({nr_dec}|{nr_hex})\b',
           '-g', 'arch/**/unistd*.h',
           '-g', 'include/uapi/asm-generic/unistd.h',
           str(kroot)]
    r = run_cmd(cmd, timeout=timeout)
    for line in r.stdout.splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            e.emit(Finding(tag='SYSCALL-NR', file=m.group(1), line=int(m.group(2)),
                           snippet=m.group(3).strip()),
                   confidence='high', source='static-rg', tags=['syscall', 'nr'])


def find_syscall_entry(e: Emitter, kroot: Path, name: str, timeout: int):
    """kernel 入口：SYSCALL_DEFINEx(name, ...)"""
    for fpath, line_no, snip in _rg_lines(
            rf'SYSCALL_DEFINE\d*\s*\(\s*{re.escape(name)}\b',
            ['*.c'], kroot, timeout):
        e.emit(Finding(tag='KERNEL-ENTRY', file=fpath, line=line_no, snippet=snip),
               confidence='high', source='static-rg', tags=['syscall', 'kernel'])

    for fpath, line_no, snip in _rg_lines(
            rf'COMPAT_SYSCALL_DEFINE\d*\s*\(\s*{re.escape(name)}\b',
            ['*.c'], kroot, timeout):
        e.emit(Finding(tag='KERNEL-COMPAT', file=fpath, line=line_no, snippet=snip),
               confidence='high', source='static-rg',
               tags=['syscall', 'kernel', 'compat'])


def find_ksys_helpers(e: Emitter, kroot: Path, name: str, timeout: int):
    """ksys_* / do_sys_* 内部辅助。"""
    patterns = [rf'\bksys_{re.escape(name)}\b', rf'\bdo_sys_{re.escape(name)}\b']
    for pat in patterns:
        for fpath, line_no, snip in _rg_lines(
                pat, ['*.c', '*.h'], kroot, timeout):
            if '(' in snip and ('{' in snip or ';' in snip or snip.endswith(',')):
                e.emit(Finding(tag='KERNEL-HELPER', file=fpath, line=line_no,
                               snippet=snip),
                       confidence='med', source='static-rg',
                       tags=['syscall', 'kernel', 'helper'])


def trace_by_name(e: Emitter, bsp_root: Path, kroot: Optional[Path],
                  arch: str, name: str, timeout: int):
    find_bionic_wrapper(e, bsp_root, name, timeout)

    if not kroot:
        print(f'# kernel 根目录未找到，跳过 kernel 侧追踪', file=sys.stderr)
        print(f'# 提示：compile_commands.json 里没有 arch/{arch}/ 记录，'
              f'可用 --kernel-root 手动指定', file=sys.stderr)
        return

    find_syscall_number(e, kroot, arch, name, timeout)
    find_syscall_entry(e, kroot, name, timeout)
    find_ksys_helpers(e, kroot, name, timeout)


def main():
    p = make_parser('syscall trace: userspace → __NR_* → kernel')
    p.add_argument('name', nargs='?', help='syscall 名（如 openat）')
    p.add_argument('--name', dest='name_opt', help='同上（可选显式）')
    p.add_argument('--nr', help='syscall 号（十进制或 0x... 十六进制）')
    p.add_argument('--arch', choices=['arm', 'arm64'], default='arm64',
                   help='目标架构（默认 arm64）')
    p.add_argument('--kernel-root', type=Path, default=None,
                   help='kernel 源根；缺省从 compile_commands.json 自动探测')
    args = p.parse_args()

    name = args.name or args.name_opt
    if not name and not args.nr:
        p.print_help(); sys.exit(1)

    with Emitter(args, Path(__file__).name) as e:
        bsp_root = Path(args.root) if args.root else (e.bsp_root or Path.cwd())
        kroot = args.kernel_root or find_kernel_root(bsp_root, args.arch)
        if kroot:
            print(f'# kernel root: {kroot}', file=sys.stderr)
        else:
            print(f'# kernel root: (未找到)', file=sys.stderr)

        if args.nr:
            nr = int(args.nr, 0)
            if kroot:
                find_syscall_by_nr(e, kroot, nr, args.timeout)
            else:
                print(f'# 需要 kernel-root 才能按号反查', file=sys.stderr)
                sys.exit(1)

        if name:
            trace_by_name(e, bsp_root, kroot, args.arch, name, args.timeout)


if __name__ == '__main__':
    main()
