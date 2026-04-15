#!/usr/bin/env python3
"""
Kconfig ↔ 代码追踪：CONFIG_XXX 从 defconfig 到 Kconfig 定义到代码使用。

用法：
  # 追踪单个 CONFIG 符号
  kconfig_trace.py --config CONFIG_VIDEO_IMX219

  # 不带 CONFIG_ 前缀也行
  kconfig_trace.py --config VIDEO_IMX219

  # 全量扫描 defconfig 里的 =y/=m 项
  kconfig_trace.py --scan --defconfig arch/arm64/configs/vendor_defconfig [--out .kconfig.idx]

识别链路：
  1. defconfig 设值        CONFIG_XXX=y/m/n
  2. Kconfig 定义          config XXX + help text + depends on
  3. Makefile 条件编译      obj-$(CONFIG_XXX) += foo.o
  4. C 源码 #ifdef/#if      #ifdef CONFIG_XXX / IS_ENABLED(CONFIG_XXX)

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


def trace_config(root: Path, symbol: str):
    """追踪单个 CONFIG 符号的完整链路。"""
    # 标准化：确保带 CONFIG_ 前缀
    bare = symbol.removeprefix('CONFIG_')
    full = f'CONFIG_{bare}'

    # 1. defconfig 设值
    for f, l, c in _rg(root, rf'^{re.escape(full)}[=\s]',
                        ['*defconfig*', '*_defconfig']):
        emit('DEFCONFIG', f'{f}:{l}', c)

    # .config（如果存在）
    for f, l, c in _rg(root, rf'^{re.escape(full)}[=\s]',
                        ['.config']):
        emit('DOT-CONFIG', f'{f}:{l}', c)

    # 2. Kconfig 定义：config BARE_NAME
    for f, l, c in _rg(root, rf'^\s*config\s+{re.escape(bare)}\s*$',
                        ['Kconfig', 'Kconfig.*']):
        emit('KCONFIG-DEF', f'{f}:{l}', c)

    # menuconfig
    for f, l, c in _rg(root, rf'^\s*menuconfig\s+{re.escape(bare)}\s*$',
                        ['Kconfig', 'Kconfig.*']):
        emit('KCONFIG-DEF', f'{f}:{l}', c)

    # depends on / select 引用
    for f, l, c in _rg(root, rf'(depends on|select|imply)\s+.*\b{re.escape(bare)}\b',
                        ['Kconfig', 'Kconfig.*']):
        emit('KCONFIG-REF', f'{f}:{l}', c)

    # 3. Makefile 条件编译
    for f, l, c in _rg(root, rf'obj-\$\({re.escape(full)}\)',
                        ['Makefile', 'Makefile.*', '*.mk']):
        emit('MAKEFILE-OBJ', f'{f}:{l}', c)

    # 也搜 ccflags-$(CONFIG_XXX)
    for f, l, c in _rg(root, rf'\$\({re.escape(full)}\)',
                        ['Makefile', 'Makefile.*']):
        if 'obj-' not in c:  # 避免和上面重复
            emit('MAKEFILE-REF', f'{f}:{l}', c)

    # 4. C 源码
    for f, l, c in _rg(root, rf'#\s*if.*\b{re.escape(full)}\b',
                        ['*.c', '*.h']):
        emit('CODE-IFDEF', f'{f}:{l}', c)

    for f, l, c in _rg(root, rf'IS_ENABLED\s*\(\s*{re.escape(full)}\s*\)',
                        ['*.c', '*.h']):
        emit('CODE-IS-ENABLED', f'{f}:{l}', c)

    for f, l, c in _rg(root, rf'IS_BUILTIN\s*\(\s*{re.escape(full)}\s*\)',
                        ['*.c', '*.h']):
        emit('CODE-IS-BUILTIN', f'{f}:{l}', c)

    for f, l, c in _rg(root, rf'IS_MODULE\s*\(\s*{re.escape(full)}\s*\)',
                        ['*.c', '*.h']):
        emit('CODE-IS-MODULE', f'{f}:{l}', c)


def do_scan(root: Path, defconfig: Optional[str], out_path: Optional[Path]):
    """扫描 defconfig 里所有 =y/=m 的 CONFIG 项。"""
    lines = []
    if defconfig:
        dc_path = root / defconfig if not Path(defconfig).is_absolute() else Path(defconfig)
    else:
        # 尝试找第一个 defconfig
        candidates = list(root.glob('arch/*/configs/*_defconfig'))
        if not candidates:
            print('未找到 defconfig，请用 --defconfig 指定', file=sys.stderr)
            sys.exit(1)
        dc_path = candidates[0]
        print(f'使用 {dc_path}', file=sys.stderr)

    if not dc_path.exists():
        print(f'{dc_path} 不存在', file=sys.stderr)
        sys.exit(1)

    for line in dc_path.read_text().splitlines():
        m = re.match(r'^(CONFIG_\w+)=([ym])', line)
        if m:
            lines.append(f'DEFCONFIG\t{dc_path}\t{m.group(1)}={m.group(2)}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='Kconfig ↔ 代码追踪')
    ap.add_argument('--config', '-c', help='CONFIG 符号（如 CONFIG_VIDEO_IMX219 或 VIDEO_IMX219）')
    ap.add_argument('--scan', action='store_true', help='扫描 defconfig 所有 =y/=m')
    ap.add_argument('--defconfig', help='defconfig 路径（--scan 时用）')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.defconfig, args.out)
    elif args.config:
        trace_config(args.root, args.config)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
