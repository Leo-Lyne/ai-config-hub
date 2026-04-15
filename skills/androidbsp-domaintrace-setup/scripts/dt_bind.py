#!/usr/bin/env python3
"""
Device Tree ↔ Driver 跨边界追踪：compatible 字符串双向映射 + DT property 追踪。

用法：
  # 从 compatible 字符串追踪（双向：DTS 节点 + driver of_match_table）
  dt_bind.py --compatible "vendor,foo-sensor"

  # 从 driver 名追踪：找 of_match_table 里的 compatible，再反查 DTS
  dt_bind.py --driver foo_driver

  # DT property 追踪：找 of_property_read_* 使用和 DTS 定义
  dt_bind.py --property "clock-frequency"

  # 全量扫描：列出所有 DTS compatible 和 driver of_device_id 对
  dt_bind.py --scan [--out .dt_bind.idx]

识别链路：
  1. DTS/DTSI 节点   compatible = "vendor,foo"
  2. Driver          .compatible = "vendor,foo" (of_device_id / of_match_table)
  3. probe 函数      platform_driver / i2c_driver / spi_driver 等注册的 .probe
  4. module 宏       module_platform_driver / module_i2c_driver 等

依赖：rg, fd。
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Finding, Emitter, make_parser, rg_find, require_version,
)

require_version("1.0.0")


def find_dts_compatible(e: Emitter, root: Path, compat: str):
    """在 DTS/DTSI 文件中查找 compatible = "..., <compat>, ..." """
    pattern = rf'compatible\s*=\s*[^;]*"{re.escape(compat)}"'
    for f, l, snip in rg_find(pattern, globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='DTS-NODE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['dt', 'compatible'])


def find_driver_compatible(e: Emitter, root: Path, compat: str):
    """在 C 源码中查找 of_device_id / of_match_table 里的 .compatible = "xxx" """
    pattern = rf'\.compatible\s*=\s*"{re.escape(compat)}"'
    for f, l, snip in rg_find(pattern, globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='DRIVER-COMPAT', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['dt', 'driver'])
        _find_probe_in_file(e, f)


def _find_probe_in_file(e: Emitter, fpath: str):
    """给定一个 driver 文件，找其中的 probe 函数定义和 module 注册宏。"""
    # .probe = xxx
    for _, l, snip in rg_find(r'\.probe\s*=\s*(\w+)', root=Path(fpath)):
        e.emit(Finding(tag='PROBE-BIND', file=fpath, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['dt', 'probe'])

    for _, l, snip in rg_find(
            r'module_(platform|i2c|spi|pci|usb|sdio)_driver\s*\(',
            root=Path(fpath)):
        e.emit(Finding(tag='MODULE-REG', file=fpath, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['dt', 'module'])

    for _, l, snip in rg_find(
            r'(platform_driver_register|i2c_add_driver|spi_register_driver|pci_register_driver)\s*\(',
            root=Path(fpath)):
        e.emit(Finding(tag='MODULE-REG', file=fpath, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['dt', 'module'])


def trace_compatible(e: Emitter, root: Path, compat: str):
    """双向追踪：DTS 节点 + driver 匹配。"""
    find_dts_compatible(e, root, compat)
    find_driver_compatible(e, root, compat)


def trace_driver(e: Emitter, root: Path, driver_name: str):
    """从 driver struct 名（如 foo_driver）找 of_match_table 里的 compatible，再追 DTS。"""
    pattern = rf'\b{re.escape(driver_name)}\b.*=\s*\{{'
    driver_files = set()
    for f, l, snip in rg_find(pattern, globs=['*.c'], root=root):
        e.emit(Finding(tag='DRIVER-DEF', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['dt', 'driver'])
        driver_files.add(f)

    compats = set()
    for fpath in driver_files:
        for _, l, snip in rg_find(r'\.compatible\s*=\s*"([^"]+)"',
                                  root=Path(fpath)):
            e.emit(Finding(tag='DRIVER-COMPAT', file=fpath, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['dt', 'driver'])
            cm = re.search(r'"([^"]+)"', snip)
            if cm:
                compats.add(cm.group(1))
        _find_probe_in_file(e, fpath)

    for c in compats:
        find_dts_compatible(e, root, c)


def trace_property(e: Emitter, root: Path, prop_name: str):
    """追踪 DT property：of_property_read_* 使用 + DTS 里定义。"""
    for f, l, snip in rg_find(
            rf'of_property_read\w*\s*\([^)]*"{re.escape(prop_name)}"',
            globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='PROP-READ', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['dt', 'property'])

    for f, l, snip in rg_find(
            rf'of_(find|get)_property\s*\([^)]*"{re.escape(prop_name)}"',
            globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='PROP-READ', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['dt', 'property'])

    for f, l, snip in rg_find(
            rf'^\s*{re.escape(prop_name)}\s*[=;]',
            globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='DTS-PROP', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['dt', 'property'])

    bindings_dir = root / 'Documentation' / 'devicetree' / 'bindings'
    search_root = bindings_dir if bindings_dir.exists() else root
    for f, l, snip in rg_find(rf'\b{re.escape(prop_name)}\b',
                              globs=['*.yaml', '*.txt'], root=search_root,
                              timeout=30):
        flower = f.lower()
        if 'devicetree' in flower or 'bindings' in flower:
            e.emit(Finding(tag='DT-BINDING-DOC', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg', tags=['dt', 'bindings'])


def trace_overlay(e: Emitter, root: Path, target: str):
    """DTBO overlay 追踪（Layer 1：纯文本搜索）。"""
    esc = re.escape(target)

    # 找所有 overlay 文件
    from _bsp_common import run_cmd
    r = run_cmd(['rg', '-l', '__overlay__', '-g', '*.dts', '-g', '*.dtsi', str(root)])
    overlay_files = r.stdout.strip().splitlines() if r.returncode in (0, 1) else []

    for f in overlay_files:
        for _, l, snip in rg_find(rf'target-path\s*=\s*"[^"]*{esc}[^"]*"',
                                  root=Path(f)):
            e.emit(Finding(tag='DTBO-TARGET-PATH', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['dt', 'overlay'])

    for f in overlay_files:
        for _, l, snip in rg_find(rf'target\s*=\s*<&\w*{esc}\w*>',
                                  root=Path(f)):
            e.emit(Finding(tag='DTBO-TARGET-REF', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['dt', 'overlay'])

    for f in overlay_files:
        for _, l, snip in rg_find(esc, root=Path(f)):
            if 'target' not in snip:
                e.emit(Finding(tag='DTBO-PROP', file=f, line=l, snippet=snip),
                       confidence='low', source='static-rg', tags=['dt', 'overlay'])

    if not overlay_files:
        print('# 未找到包含 __overlay__ 的 DTS 文件', file=sys.stderr)

    # base DT 里该节点的定义
    for f, l, snip in rg_find(rf'{esc}\s*[\{{:;@]',
                              globs=['*.dts', '*.dtsi'], root=root):
        if '__overlay__' not in snip:
            e.emit(Finding(tag='DT-BASE-NODE', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg', tags=['dt', 'base'])


def do_scan(e: Emitter, root: Path, out_path: Optional[Path]):
    """全量扫描：所有 DTS compatible + driver of_device_id 配对。"""
    lines = []

    for f, l, snip in rg_find(r'compatible\s*=\s*"([^"]+)"',
                              globs=['*.dts', '*.dtsi'], root=root, timeout=300):
        for c in re.findall(r'"([^"]+)"', snip):
            lines.append(f'DTS\t{f}:{l}\t{c}')
            e.emit(Finding(tag='DTS', file=f, line=l, snippet=c),
                   confidence='high', source='static-rg', tags=['dt', 'scan'])

    for f, l, snip in rg_find(r'\.compatible\s*=\s*"([^"]+)"',
                              globs=['*.c', '*.h'], root=root, timeout=300):
        cm = re.search(r'"([^"]+)"', snip)
        if cm:
            lines.append(f'DRIVER\t{f}:{l}\t{cm.group(1)}')
            e.emit(Finding(tag='DRIVER', file=f, line=l, snippet=cm.group(1)),
                   confidence='high', source='static-rg', tags=['dt', 'scan'])

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('Device Tree ↔ Driver 跨边界追踪')
    p.add_argument('--compatible', '-c', help='compatible 字符串（如 "vendor,foo-sensor"）')
    p.add_argument('--driver', '-d', help='driver struct 名（如 foo_driver）')
    p.add_argument('--property', '-p', help='DT property 名（如 "clock-frequency"）')
    p.add_argument('--overlay', help='DTBO overlay 追踪（node 名或路径片段）')
    p.add_argument('--scan', action='store_true', help='全量扫描所有 compatible 配对')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.compatible:
            trace_compatible(e, search_root, args.compatible)
        elif args.driver:
            trace_driver(e, search_root, args.driver)
        elif args.property:
            trace_property(e, search_root, args.property)
        elif args.overlay:
            trace_overlay(e, search_root, args.overlay)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
