#!/usr/bin/env python3
"""
Firmware 加载 + Kernel 模块自动加载追踪。

用法：
  # 追踪 firmware 文件：request_firmware → 文件系统 → 打包
  firmware_trace.py --firmware "imx219.fw"

  # 追踪 kernel module：MODULE_ALIAS / MODULE_DEVICE_TABLE → of_device_id → Makefile
  firmware_trace.py --ko imx219

  # 追踪 MODULE_ALIAS 模式
  firmware_trace.py --module-alias "of:N*T*Cvendor,foo*"

  # 全量扫描
  firmware_trace.py --scan [--out .firmware.idx]

识别链路（firmware）：
  1. request_firmware(&fw, "name", dev)      内核加载请求
  2. /vendor/firmware/ 或 /lib/firmware/      文件实际位置
  3. PRODUCT_COPY_FILES / Android.mk          打包到 image

识别链路（module autoload）：
  1. MODULE_DEVICE_TABLE(of, ...)             DT match table 导出 modalias
  2. MODULE_DEVICE_TABLE(platform, ...)       platform match
  3. MODULE_ALIAS("...")                      显式 alias
  4. Makefile obj-$(CONFIG_XXX) += foo.o      编译控制

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


def trace_firmware(root: Path, fw_name: str):
    esc = re.escape(fw_name)

    # 1. kernel: request_firmware / request_firmware_nowait / firmware_request_*
    for f, l, c in _rg(root, rf'(request_firmware|firmware_request)\w*\s*\([^)]*"{esc}"',
                        ['*.c', '*.h']):
        emit('FW-REQUEST', f'{f}:{l}', c)

    # MODULE_FIRMWARE
    for f, l, c in _rg(root, rf'MODULE_FIRMWARE\s*\(\s*"{esc}"',
                        ['*.c']):
        emit('FW-MODULE', f'{f}:{l}', c)

    # 2. 文件系统里的实际固件文件
    for f, l, c in _rg(root, rf'\b{esc}\b',
                        ['*.mk', 'Android.bp']):
        emit('FW-BUILD', f'{f}:{l}', c)

    # PRODUCT_COPY_FILES 里的 firmware 引用
    for f, l, c in _rg(root, rf'PRODUCT_COPY_FILES\s*\+?=.*{esc}',
                        ['*.mk']):
        emit('FW-COPY', f'{f}:{l}', c)

    # vendor/firmware 或 lib/firmware 路径
    for f, l, c in _rg(root, rf'(vendor|lib)/firmware/.*{esc}',
                        ['*.mk', '*.bp', '*.rc', 'Makefile']):
        emit('FW-PATH', f'{f}:{l}', c)


def trace_ko(root: Path, module_name: str):
    """追踪内核模块：Makefile → CONFIG → MODULE_DEVICE_TABLE。"""
    esc = re.escape(module_name)

    # Makefile: obj-$(CONFIG_XXX) += module.o
    for f, l, c in _rg(root, rf'obj-.*\+=\s*{esc}\.o',
                        ['Makefile', 'Makefile.*']):
        emit('KO-MAKEFILE', f'{f}:{l}', c)

    # 从 Makefile 里提取 CONFIG 符号
    for f, l, c in _rg(root, rf'obj-\$\((CONFIG_\w+)\)\s*\+=\s*{esc}\.o',
                        ['Makefile']):
        cm = re.search(r'CONFIG_\w+', c)
        if cm:
            emit('KO-CONFIG', f'{f}:{l}', cm.group(0))

    # 同目录源文件里的 MODULE_DEVICE_TABLE
    for f, l, c in _rg(root, rf'MODULE_DEVICE_TABLE\s*\(\s*\w+',
                        ['*.c']):
        if module_name in f.lower() or module_name.replace('-', '_') in f.lower():
            emit('KO-DEVICE-TABLE', f'{f}:{l}', c)

    # MODULE_ALIAS
    for f, l, c in _rg(root, rf'MODULE_ALIAS\s*\(',
                        ['*.c']):
        if module_name in f.lower() or module_name.replace('-', '_') in f.lower():
            emit('KO-ALIAS', f'{f}:{l}', c)

    # module_init / module_exit
    for f, l, c in _rg(root, rf'module_(init|exit)\s*\(',
                        ['*.c']):
        if module_name in f.lower() or module_name.replace('-', '_') in f.lower():
            emit('KO-INIT', f'{f}:{l}', c)


def trace_module_alias(root: Path, alias: str):
    esc = re.escape(alias)

    # MODULE_ALIAS("...")
    for f, l, c in _rg(root, rf'MODULE_ALIAS\s*\(\s*"{esc}"',
                        ['*.c']):
        emit('ALIAS-DEF', f'{f}:{l}', c)

    # MODULE_DEVICE_TABLE 生成的 alias 模式更复杂，尽力搜
    for f, l, c in _rg(root, rf'{esc}',
                        ['modules.alias', 'modules.alias.bin']):
        emit('ALIAS-FILE', f'{f}:{l}', c)


def do_scan(root: Path, out_path: Optional[Path]):
    lines = []

    # 所有 request_firmware 调用
    for f, l, c in _rg(root, r'request_firmware\w*\s*\([^,]+,\s*"([^"]+)"',
                        ['*.c'], timeout=300):
        cm = re.search(r'"([^"]+)"', c)
        if cm:
            lines.append(f'FW-REQUEST\t{f}:{l}\t{cm.group(1)}')

    # 所有 MODULE_FIRMWARE
    for f, l, c in _rg(root, r'MODULE_FIRMWARE\s*\(\s*"([^"]+)"',
                        ['*.c'], timeout=300):
        cm = re.search(r'"([^"]+)"', c)
        if cm:
            lines.append(f'FW-MODULE\t{f}:{l}\t{cm.group(1)}')

    # 所有 MODULE_DEVICE_TABLE
    for f, l, c in _rg(root, r'MODULE_DEVICE_TABLE\s*\(\s*(\w+)',
                        ['*.c'], timeout=300):
        cm = re.search(r'MODULE_DEVICE_TABLE\s*\(\s*(\w+)', c)
        if cm:
            lines.append(f'MODULE-TABLE\t{f}:{l}\t{cm.group(1)}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='Firmware 加载 + Kernel 模块追踪')
    ap.add_argument('--firmware', '-f', help='firmware 文件名（如 "imx219.fw"）')
    ap.add_argument('--ko', '-k', help='内核模块名（如 imx219）')
    ap.add_argument('--module-alias', help='MODULE_ALIAS 模式')
    ap.add_argument('--scan', action='store_true', help='全量扫描')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.firmware:
        trace_firmware(args.root, args.firmware)
    elif args.ko:
        trace_ko(args.root, args.ko)
    elif args.module_alias:
        trace_module_alias(args.root, args.module_alias)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
