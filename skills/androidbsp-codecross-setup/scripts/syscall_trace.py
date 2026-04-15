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

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


def run(cmd, timeout=60, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ''


def emit(tag: str, location: str, info: str = ''):
    print(f'{tag}\t{location}\t{info}')


def find_kernel_root(bsp_root: Path, arch: str) -> Optional[Path]:
    """
    优先从 compile_commands.json 解析出 kernel 源根。
    回退：按常见命名猜测。
    """
    ccj = bsp_root / 'compile_commands.json'
    if ccj.exists():
        try:
            data = json.loads(ccj.read_text())
            # 找任何一条含 arch/<arch>/ 的记录，反推 kernel 根
            marker = f'/arch/{arch}/'
            for entry in data:
                f = entry.get('file', '')
                d = entry.get('directory', '')
                for s in (f, d):
                    idx = s.find(marker)
                    if idx > 0:
                        # 截到 arch/ 之前一级，就是 kernel 根
                        kroot = Path(s[:idx])
                        if (kroot / 'arch' / arch).is_dir():
                            return kroot
        except (json.JSONDecodeError, OSError):
            pass

    # 回退探测
    candidates = [
        bsp_root / 'kernel',
        bsp_root / 'kernel_platform' / 'msm-kernel',
        bsp_root / 'kernel_platform' / 'common',
    ]
    # kernel-*/kernel-5.10 之类
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


def find_bionic_wrapper(bsp_root: Path, name: str):
    """
    bionic 的 syscall wrapper 有两种形态：
      1. bionic/libc/arch-*/syscalls/<name>.S  (逐 syscall 一个 .S)
      2. bionic/libc/SYSCALLS.TXT              (生成器来源)
    """
    # .S 文件
    for s in run(['fd', '--type', 'f', f'^{re.escape(name)}\\.S$',
                  str(bsp_root / 'bionic') if (bsp_root / 'bionic').exists() else str(bsp_root)]).splitlines():
        if s.strip():
            emit('USER-WRAPPER', s.strip(), f'bionic .S stub for {name}')

    # SYSCALLS.TXT
    for line in run(['rg', '-n', rf'\b{re.escape(name)}\b',
                     '-g', 'SYSCALLS.TXT', str(bsp_root)]).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('USER-WRAPPER', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def find_syscall_number(kroot: Path, arch: str, name: str):
    """
    syscall 号来源：
      arm64: arch/arm64/include/asm/unistd.h 经过 asm-generic/unistd.h 包展开
             编译产物 include/generated/uapi/asm/unistd_64.h
      arm:   arch/arm/include/uapi/asm/unistd.h 或 unistd-common.h
    """
    globs = []
    if arch == 'arm64':
        globs = [
            'arch/arm64/include/**/unistd*.h',
            'include/uapi/asm-generic/unistd.h',
        ]
    else:  # arm
        globs = [
            'arch/arm/include/**/unistd*.h',
            'arch/arm/tools/syscall.tbl',
            'include/uapi/asm-generic/unistd.h',
        ]

    for g in globs:
        args = ['rg', '-n', rf'\b__NR_{re.escape(name)}\b', '-g', g, str(kroot)]
        for line in run(args).splitlines():
            m = re.match(r'^([^:]+):(\d+):(.*)$', line)
            if m:
                emit('SYSCALL-NR', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # arm 还有 syscall.tbl
    if arch == 'arm':
        tbl = kroot / 'arch' / 'arm' / 'tools' / 'syscall.tbl'
        if tbl.exists():
            for i, ln in enumerate(tbl.read_text().splitlines(), 1):
                parts = ln.split()
                if len(parts) >= 4 and parts[3] == f'sys_{name}':
                    emit('SYSCALL-NR', f'{tbl}:{i}', ln.strip())


def find_syscall_by_nr(kroot: Path, nr: int):
    """从号反查名字。arch 不影响 grep 模式（unistd.h 都用 __NR_ 前缀）。"""
    nr_dec = str(nr)
    nr_hex = hex(nr)
    # unistd*.h 里形如 #define __NR_openat 56
    args = ['rg', '-n', rf'#define\s+__NR_\w+\s+({nr_dec}|{nr_hex})\b',
            '-g', 'arch/**/unistd*.h',
            '-g', 'include/uapi/asm-generic/unistd.h',
            str(kroot)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('SYSCALL-NR', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def find_syscall_entry(kroot: Path, name: str):
    """
    kernel 入口：SYSCALL_DEFINEx(name, ...) 宏。
    典型位置：fs/*.c, kernel/*.c, mm/*.c 等。
    """
    # SYSCALL_DEFINE0..6
    args = ['rg', '-n', rf'SYSCALL_DEFINE\d*\s*\(\s*{re.escape(name)}\b',
            '-g', '*.c', str(kroot)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('KERNEL-ENTRY', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # COMPAT_SYSCALL_DEFINE
    args = ['rg', '-n', rf'COMPAT_SYSCALL_DEFINE\d*\s*\(\s*{re.escape(name)}\b',
            '-g', '*.c', str(kroot)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('KERNEL-COMPAT', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def find_ksys_helpers(kroot: Path, name: str):
    """内部 ksys_* / do_sys_* 辅助函数（通常是 SYSCALL_DEFINE 的实际实现）。"""
    patterns = [rf'\bksys_{re.escape(name)}\b', rf'\bdo_sys_{re.escape(name)}\b']
    for pat in patterns:
        # 只看定义：前面有返回类型，不是 extern
        args = ['global', '-x', '-d', f'ksys_{name}']
        # 用 rg 更保险
        args = ['rg', '-n', pat, '-g', '*.c', '-g', '*.h', str(kroot)]
        for line in run(args).splitlines():
            m = re.match(r'^([^:]+):(\d+):(.*)$', line)
            if m:
                snippet = m.group(3).strip()
                # 粗筛：看起来是定义/声明而非调用
                if '(' in snippet and ('{' in snippet or ';' in snippet or snippet.endswith(',')):
                    emit('KERNEL-HELPER', f'{m.group(1)}:{m.group(2)}', snippet)


def trace_by_name(bsp_root: Path, kroot: Optional[Path], arch: str, name: str):
    # 1. userspace wrapper
    find_bionic_wrapper(bsp_root, name)

    if not kroot:
        print(f'# kernel 根目录未找到，跳过 kernel 侧追踪', file=sys.stderr)
        print(f'# 提示：compile_commands.json 里没有 arch/{arch}/ 记录，'
              f'可用 --kernel-root 手动指定', file=sys.stderr)
        return

    # 2. syscall 号
    find_syscall_number(kroot, arch, name)
    # 3. SYSCALL_DEFINE 入口
    find_syscall_entry(kroot, name)
    # 4. ksys_/do_sys_ 辅助
    find_ksys_helpers(kroot, name)


def main():
    ap = argparse.ArgumentParser(description='syscall 跨边界追踪')
    ap.add_argument('name', nargs='?', help='syscall 名（如 openat）')
    ap.add_argument('--name', dest='name_opt', help='同上（可选显式）')
    ap.add_argument('--nr', help='syscall 号（十进制或 0x... 十六进制）')
    ap.add_argument('--arch', choices=['arm', 'arm64'], default='arm64',
                    help='目标架构（默认 arm64）')
    ap.add_argument('--root', type=Path, default=Path.cwd(),
                    help='BSP 根目录（默认当前目录）')
    ap.add_argument('--kernel-root', type=Path, default=None,
                    help='kernel 源根；缺省从 compile_commands.json 自动探测')
    args = ap.parse_args()

    name = args.name or args.name_opt
    if not name and not args.nr:
        ap.print_help(); sys.exit(1)

    kroot = args.kernel_root or find_kernel_root(args.root, args.arch)
    if kroot:
        print(f'# kernel root: {kroot}', file=sys.stderr)
    else:
        print(f'# kernel root: (未找到)', file=sys.stderr)

    if args.nr:
        nr = int(args.nr, 0)
        if kroot:
            find_syscall_by_nr(kroot, nr)
        else:
            print(f'# 需要 kernel-root 才能按号反查', file=sys.stderr)
            sys.exit(1)

    if name:
        trace_by_name(args.root, kroot, args.arch, name)


if __name__ == '__main__':
    main()
