#!/usr/bin/env python3
"""
Firmware 加载 + Kernel 模块自动加载追踪。

用法：
  firmware_trace.py --firmware "imx219.fw"
  firmware_trace.py --ko imx219
  firmware_trace.py --module-alias "of:N*T*Cvendor,foo*"
  firmware_trace.py --scan [--out .firmware.idx]

Task 11 扩展：
  - 多 firmware 路径：vendor/firmware、vendor_dlkm/firmware、odm/firmware、
    system/etc/firmware、product/etc/firmware，均尝试
  - 解析 vendor / vendor_dlkm / odm 各自的 lib/modules/modules.load，
    写 MOD-LOAD finding 反映开机自动加载顺序

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


FW_PARTITION_SUBPATHS = [
    'firmware',
    'etc/firmware',
    'lib/firmware',
]


def _firmware_roots(bsp_root: Path) -> list[Path]:
    """返回所有可能的 firmware 目录。"""
    roots: list[Path] = []
    for sub in FW_PARTITION_SUBPATHS:
        for part_dir in scan_partitions(bsp_root, sub):
            roots.append(part_dir)
    # vendor_dlkm 不在标准 PARTITIONS 里，显式处理
    for p in (bsp_root / 'vendor_dlkm' / 'firmware',
              bsp_root / 'vendor_dlkm' / 'etc' / 'firmware'):
        if p.exists():
            roots.append(p)
    return roots


def _modules_load_files(bsp_root: Path) -> list[Path]:
    """收集 vendor / vendor_dlkm / odm 下的 modules.load*。"""
    files: list[Path] = []
    bases = [
        bsp_root / 'vendor' / 'lib' / 'modules',
        bsp_root / 'vendor_dlkm' / 'lib' / 'modules',
        bsp_root / 'odm' / 'lib' / 'modules',
    ]
    for b in bases:
        if b.is_dir():
            for name in ('modules.load', 'modules.load.recovery',
                         'modules.blocklist'):
                cand = b / name
                if cand.exists():
                    files.append(cand)
            # 有时放在 <kver>/ 子目录
            for sub in b.glob('*/modules.load*'):
                if sub.is_file():
                    files.append(sub)
    return files


def _emit_modules_load(e: Emitter, bsp_root: Path):
    """解析 modules.load* 并输出 MOD-LOAD findings。"""
    for lf in _modules_load_files(bsp_root):
        try:
            for idx, line in enumerate(lf.read_text(errors='ignore').splitlines(), 1):
                ln = line.strip()
                if not ln or ln.startswith('#'):
                    continue
                e.emit(Finding(tag='MOD-LOAD', file=str(lf),
                               line=idx, snippet=ln),
                       confidence='high', source='static-fs',
                       tags=['firmware', 'modules.load'])
        except OSError:
            continue


def trace_firmware(e: Emitter, bsp_root: Path, fw_name: str):
    esc = re.escape(fw_name)

    # 1. kernel: request_firmware 等
    for f, l, snip in rg_find(
            rf'(request_firmware|firmware_request)\w*\s*\([^)]*"{esc}"',
            globs=['*.c', '*.h'], root=bsp_root):
        e.emit(Finding(tag='FW-REQUEST', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['firmware'])

    for f, l, snip in rg_find(rf'MODULE_FIRMWARE\s*\(\s*"{esc}"',
                              globs=['*.c'], root=bsp_root):
        e.emit(Finding(tag='FW-MODULE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['firmware'])

    # 2. 实际 firmware 文件：多分区探测
    for fw_root in _firmware_roots(bsp_root):
        for p in fw_root.rglob(fw_name):
            if p.is_file():
                e.emit(Finding(tag='FW-INSTALLED', file=str(p), line=0,
                               snippet=f'installed under {fw_root}'),
                       confidence='high', source='static-fs',
                       tags=['firmware', 'installed'])

    # 3. build 引用
    for f, l, snip in rg_find(rf'\b{esc}\b',
                              globs=['*.mk', 'Android.bp'], root=bsp_root):
        e.emit(Finding(tag='FW-BUILD', file=f, line=l, snippet=snip),
               confidence='low', source='static-rg', tags=['firmware', 'build'])

    for f, l, snip in rg_find(rf'PRODUCT_COPY_FILES\s*\+?=.*{esc}',
                              globs=['*.mk'], root=bsp_root):
        e.emit(Finding(tag='FW-COPY', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['firmware', 'build'])

    for f, l, snip in rg_find(rf'(vendor|lib)/firmware/.*{esc}',
                              globs=['*.mk', '*.bp', '*.rc', 'Makefile'],
                              root=bsp_root):
        e.emit(Finding(tag='FW-PATH', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['firmware', 'path'])

    # 4. 附带输出 modules.load
    _emit_modules_load(e, bsp_root)


def trace_ko(e: Emitter, bsp_root: Path, module_name: str):
    esc = re.escape(module_name)

    for f, l, snip in rg_find(rf'obj-.*\+=\s*{esc}\.o',
                              globs=['Makefile', 'Makefile.*'], root=bsp_root):
        e.emit(Finding(tag='KO-MAKEFILE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['ko', 'makefile'])

    for f, l, snip in rg_find(
            rf'obj-\$\((CONFIG_\w+)\)\s*\+=\s*{esc}\.o',
            globs=['Makefile'], root=bsp_root):
        cm = re.search(r'CONFIG_\w+', snip)
        if cm:
            e.emit(Finding(tag='KO-CONFIG', file=f, line=l, snippet=cm.group(0)),
                   confidence='high', source='static-rg',
                   tags=['ko', 'kconfig'])

    for f, l, snip in rg_find(r'MODULE_DEVICE_TABLE\s*\(\s*\w+',
                              globs=['*.c'], root=bsp_root):
        if module_name in f.lower() or module_name.replace('-', '_') in f.lower():
            e.emit(Finding(tag='KO-DEVICE-TABLE', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg', tags=['ko'])

    for f, l, snip in rg_find(r'MODULE_ALIAS\s*\(',
                              globs=['*.c'], root=bsp_root):
        if module_name in f.lower() or module_name.replace('-', '_') in f.lower():
            e.emit(Finding(tag='KO-ALIAS', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg', tags=['ko'])

    for f, l, snip in rg_find(r'module_(init|exit)\s*\(',
                              globs=['*.c'], root=bsp_root):
        if module_name in f.lower() or module_name.replace('-', '_') in f.lower():
            e.emit(Finding(tag='KO-INIT', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg', tags=['ko'])

    # modules.load 里是否列入
    _emit_modules_load(e, bsp_root)


def trace_module_alias(e: Emitter, bsp_root: Path, alias: str):
    esc = re.escape(alias)

    for f, l, snip in rg_find(rf'MODULE_ALIAS\s*\(\s*"{esc}"',
                              globs=['*.c'], root=bsp_root):
        e.emit(Finding(tag='ALIAS-DEF', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['ko', 'alias'])

    for f, l, snip in rg_find(rf'{esc}',
                              globs=['modules.alias', 'modules.alias.bin'],
                              root=bsp_root):
        e.emit(Finding(tag='ALIAS-FILE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['ko', 'alias'])


def do_scan(e: Emitter, bsp_root: Path, out_path: Optional[Path]):
    lines = []

    for f, l, snip in rg_find(
            r'request_firmware\w*\s*\([^,]+,\s*"([^"]+)"',
            globs=['*.c'], root=bsp_root, timeout=300):
        cm = re.search(r'"([^"]+)"', snip)
        if cm:
            lines.append(f'FW-REQUEST\t{f}:{l}\t{cm.group(1)}')

    for f, l, snip in rg_find(r'MODULE_FIRMWARE\s*\(\s*"([^"]+)"',
                              globs=['*.c'], root=bsp_root, timeout=300):
        cm = re.search(r'"([^"]+)"', snip)
        if cm:
            lines.append(f'FW-MODULE\t{f}:{l}\t{cm.group(1)}')

    for f, l, snip in rg_find(r'MODULE_DEVICE_TABLE\s*\(\s*(\w+)',
                              globs=['*.c'], root=bsp_root, timeout=300):
        cm = re.search(r'MODULE_DEVICE_TABLE\s*\(\s*(\w+)', snip)
        if cm:
            lines.append(f'MODULE-TABLE\t{f}:{l}\t{cm.group(1)}')

    # 同时把 modules.load 全量输出
    _emit_modules_load(e, bsp_root)

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('Firmware 加载 + Kernel 模块追踪（多分区）')
    p.add_argument('--firmware', '-f', help='firmware 文件名')
    p.add_argument('--ko', '-k', help='内核模块名')
    p.add_argument('--module-alias', dest='module_alias',
                   help='MODULE_ALIAS 模式')
    p.add_argument('--scan', action='store_true', help='全量扫描')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.firmware:
            trace_firmware(e, search_root, args.firmware)
        elif args.ko:
            trace_ko(e, search_root, args.ko)
        elif args.module_alias:
            trace_module_alias(e, search_root, args.module_alias)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
