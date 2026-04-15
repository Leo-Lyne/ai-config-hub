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


def find_dts_compatible(root: Path, compat: str):
    """在 DTS/DTSI 文件中查找 compatible = "..., <compat>, ..." """
    # DTS 里 compatible 可能是多值：compatible = "vendor,foo", "vendor,bar";
    args = ['rg', '-n', '--no-heading',
            rf'compatible\s*=\s*[^;]*"{re.escape(compat)}"',
            '-g', '*.dts', '-g', '*.dtsi', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('DTS-NODE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def find_driver_compatible(root: Path, compat: str):
    """在 C 源码中查找 of_device_id / of_match_table 里的 .compatible = "xxx" """
    args = ['rg', '-n', '--no-heading',
            rf'\.compatible\s*=\s*"{re.escape(compat)}"',
            '-g', '*.c', '-g', '*.h', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('DRIVER-COMPAT', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())
            # 顺带找同文件里的 probe 函数和 module 宏
            _find_probe_in_file(m.group(1))


def _find_probe_in_file(fpath: str):
    """给定一个 driver 文件，找其中的 probe 函数定义和 module 注册宏。"""
    # .probe = xxx
    for line in run(['rg', '-n', r'\.probe\s*=\s*(\w+)', fpath]).splitlines():
        m = re.match(r'^(\d+):(.*)$', line)
        if m:
            emit('PROBE-BIND', f'{fpath}:{m.group(1)}', m.group(2).strip())

    # module_platform_driver / module_i2c_driver / module_spi_driver 等
    for line in run(['rg', '-n',
                     r'module_(platform|i2c|spi|pci|usb|sdio)_driver\s*\(',
                     fpath]).splitlines():
        m = re.match(r'^(\d+):(.*)$', line)
        if m:
            emit('MODULE-REG', f'{fpath}:{m.group(1)}', m.group(2).strip())

    # platform_driver_register / i2c_add_driver 等
    for line in run(['rg', '-n',
                     r'(platform_driver_register|i2c_add_driver|spi_register_driver|pci_register_driver)\s*\(',
                     fpath]).splitlines():
        m = re.match(r'^(\d+):(.*)$', line)
        if m:
            emit('MODULE-REG', f'{fpath}:{m.group(1)}', m.group(2).strip())


def trace_compatible(root: Path, compat: str):
    """双向追踪：DTS 节点 + driver 匹配。"""
    find_dts_compatible(root, compat)
    find_driver_compatible(root, compat)


def trace_driver(root: Path, driver_name: str):
    """从 driver struct 名（如 foo_driver）找 of_match_table 里的 compatible，再追 DTS。"""
    # 找 struct xxx_driver xxx_driver = { ... } 或 .driver = { .of_match_table = ... }
    # 策略：先找 driver struct 里引用的 of_match_table 变量名
    args = ['rg', '-n', '--no-heading',
            rf'\b{re.escape(driver_name)}\b.*=\s*\{{',
            '-g', '*.c', str(root)]
    driver_files = set()
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('DRIVER-DEF', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())
            driver_files.add(m.group(1))

    # 在这些文件里找所有 .compatible = "xxx"
    compats = set()
    for fpath in driver_files:
        for line in run(['rg', '-n', r'\.compatible\s*=\s*"([^"]+)"', fpath]).splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m:
                emit('DRIVER-COMPAT', f'{fpath}:{m.group(1)}', m.group(2).strip())
                cm = re.search(r'"([^"]+)"', m.group(2))
                if cm:
                    compats.add(cm.group(1))
        _find_probe_in_file(fpath)

    # 反查 DTS
    for c in compats:
        find_dts_compatible(root, c)


def trace_property(root: Path, prop_name: str):
    """追踪 DT property：of_property_read_* 使用 + DTS 里定义。"""
    # C 侧：of_property_read_*(..., "prop_name", ...)
    args = ['rg', '-n', '--no-heading',
            rf'of_property_read\w*\s*\([^)]*"{re.escape(prop_name)}"',
            '-g', '*.c', '-g', '*.h', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('PROP-READ', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # of_find_property / of_get_property
    args = ['rg', '-n', '--no-heading',
            rf'of_(find|get)_property\s*\([^)]*"{re.escape(prop_name)}"',
            '-g', '*.c', '-g', '*.h', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('PROP-READ', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # DTS 侧：prop_name = <...> 或 prop_name;（boolean property）
    args = ['rg', '-n', '--no-heading',
            rf'^\s*{re.escape(prop_name)}\s*[=;]',
            '-g', '*.dts', '-g', '*.dtsi', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('DTS-PROP', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # DT bindings 文档
    args = ['rg', '-n', '--no-heading',
            rf'\b{re.escape(prop_name)}\b',
            '-g', '*.yaml', '-g', '*.txt',
            str(root / 'Documentation' / 'devicetree' / 'bindings')
            if (root / 'Documentation' / 'devicetree' / 'bindings').exists()
            else str(root)]
    for line in run(args, timeout=30).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m and 'devicetree' in m.group(1).lower() or 'bindings' in m.group(1).lower():
            emit('DT-BINDING-DOC', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def trace_overlay(root: Path, target: str):
    """DTBO overlay 追踪（Layer 1：纯文本搜索）。"""
    esc = re.escape(target)

    # 找所有 overlay 文件（包含 __overlay__ 关键字的 DTS）
    args = ['rg', '-l', '__overlay__', '-g', '*.dts', '-g', '*.dtsi', str(root)]
    overlay_files = run(args).strip().splitlines()

    # 在 overlay 文件里找 target-path 匹配
    for f in overlay_files:
        for line in run(['rg', '-n', rf'target-path\s*=\s*"[^"]*{esc}[^"]*"', f]).splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m:
                emit('DTBO-TARGET-PATH', f'{f}:{m.group(1)}', m.group(2).strip())

    # target = <&phandle> 形式
    for f in overlay_files:
        for line in run(['rg', '-n', rf'target\s*=\s*<&\w*{esc}\w*>', f]).splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m:
                emit('DTBO-TARGET-REF', f'{f}:{m.group(1)}', m.group(2).strip())

    # overlay 内的 property（在 __overlay__ 块里找该 target 相关属性）
    for f in overlay_files:
        for line in run(['rg', '-n', rf'{esc}', f]).splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m and 'target' not in m.group(2):  # 避免和上面重复
                emit('DTBO-PROP', f'{f}:{m.group(1)}', m.group(2).strip())

    # 列出所有 overlay 文件（供参考）
    if not overlay_files:
        print(f'# 未找到包含 __overlay__ 的 DTS 文件', file=sys.stderr)
    else:
        for f in overlay_files[:20]:
            # 只在包含 target 的 overlay 里报
            if run(['rg', '-q', esc, f]).strip() or run(['rg', '-l', esc, f]).strip():
                pass  # 已在上面 emit
            else:
                # 列出所有 overlay 文件以供浏览
                emit('DTBO-FILE', f, '(overlay file, may be relevant)')

    # base DT 里该节点的定义
    args = ['rg', '-n', '--no-heading',
            rf'{esc}\s*[\{{:;@]',
            '-g', '*.dts', '-g', '*.dtsi', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m and '__overlay__' not in m.group(3):
            emit('DT-BASE-NODE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def do_scan(root: Path, out_path: Optional[Path]):
    """全量扫描：所有 DTS compatible + driver of_device_id 配对。"""
    lines = []

    # 扫 DTS compatible
    args = ['rg', '-n', '--no-heading',
            r'compatible\s*=\s*"([^"]+)"',
            '-g', '*.dts', '-g', '*.dtsi', str(root)]
    for line in run(args, timeout=300).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            cms = re.findall(r'"([^"]+)"', m.group(3))
            for c in cms:
                lines.append(f'DTS\t{m.group(1)}:{m.group(2)}\t{c}')

    # 扫 driver .compatible
    args = ['rg', '-n', '--no-heading',
            r'\.compatible\s*=\s*"([^"]+)"',
            '-g', '*.c', '-g', '*.h', str(root)]
    for line in run(args, timeout=300).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            cm = re.search(r'"([^"]+)"', m.group(3))
            if cm:
                lines.append(f'DRIVER\t{m.group(1)}:{m.group(2)}\t{cm.group(1)}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='Device Tree ↔ Driver 跨边界追踪')
    ap.add_argument('--compatible', '-c', help='compatible 字符串（如 "vendor,foo-sensor"）')
    ap.add_argument('--driver', '-d', help='driver struct 名（如 foo_driver）')
    ap.add_argument('--property', '-p', help='DT property 名（如 "clock-frequency"）')
    ap.add_argument('--overlay', help='DTBO overlay 追踪（node 名或路径片段）')
    ap.add_argument('--scan', action='store_true', help='全量扫描所有 compatible 配对')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.compatible:
        trace_compatible(args.root, args.compatible)
    elif args.driver:
        trace_driver(args.root, args.driver)
    elif args.property:
        trace_property(args.root, args.property)
    elif args.overlay:
        trace_overlay(args.root, args.overlay)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
