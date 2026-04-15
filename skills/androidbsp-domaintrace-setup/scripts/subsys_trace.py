#!/usr/bin/env python3
"""
Kernel 子系统资源框架追踪：clock / regulator / GPIO / IRQ / power-domain。

用法：
  # clock：追踪 clock name 的 provider → consumer → DT 定义
  subsys_trace.py --clock <name>

  # regulator：追踪 supply name 的 provider → consumer → DT *-supply
  subsys_trace.py --regulator <name>

  # GPIO：追踪 GPIO label 的 provider → consumer → DT *-gpios
  subsys_trace.py --gpio <name>

  # IRQ：追踪 IRQ 的 handler 注册 → irq_chip → DT interrupts
  subsys_trace.py --irq <name_or_label>

  # power-domain：追踪 genpd 的 provider → consumer → DT power-domains
  subsys_trace.py --power-domain <name>

  # 全量扫描
  subsys_trace.py --scan [--out .subsys.idx]

依赖：rg。
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


# ── clock ──
def trace_clock(e: Emitter, root: Path, name: str):
    esc = re.escape(name)

    for f, l, snip in rg_find(rf'(devm_)?clk_get(_optional)?\s*\([^)]*"{esc}"',
                              globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='CLK-CONSUMER', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['clock', 'consumer'])

    for f, l, snip in rg_find(rf'of_clk_get_by_name\s*\([^)]*"{esc}"',
                              globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='CLK-CONSUMER', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['clock', 'consumer'])

    for f, l, snip in rg_find(rf'clock-names\s*=\s*[^;]*"{esc}"',
                              globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='CLK-DT-NAME', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['clock', 'dt'])

    for f, l, snip in rg_find(rf'clk_(hw_)?register\w*\s*\([^)]*"{esc}"',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='CLK-PROVIDER', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['clock', 'provider'])

    for f, l, snip in rg_find(rf'CLK_OF_DECLARE\w*\s*\([^)]*"{esc}"',
                              globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='CLK-OF-DECLARE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['clock', 'provider'])


# ── regulator ──
def trace_regulator(e: Emitter, root: Path, name: str):
    esc = re.escape(name)

    for f, l, snip in rg_find(
            rf'(devm_)?regulator_get(_optional)?\s*\([^)]*"{esc}"',
            globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='REG-CONSUMER', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['regulator', 'consumer'])

    for f, l, snip in rg_find(rf'\.(name|supply_name)\s*=\s*"{esc}"',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='REG-PROVIDER', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['regulator', 'provider'])

    for f, l, snip in rg_find(rf'{esc}-supply\s*=',
                              globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='REG-DT-SUPPLY', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['regulator', 'dt'])

    for f, l, snip in rg_find(rf'regulator-name\s*=\s*"{esc}"',
                              globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='REG-DT-NAME', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['regulator', 'dt'])


# ── GPIO ──
def trace_gpio(e: Emitter, root: Path, name: str):
    esc = re.escape(name)

    for f, l, snip in rg_find(
            rf'(devm_)?gpiod_get(_optional|_index)?\s*\([^)]*"{esc}"',
            globs=['*.c', '*.h'], root=root):
        e.emit(Finding(tag='GPIO-CONSUMER', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['gpio', 'consumer'])

    for f, l, snip in rg_find(rf'of_get_named_gpio\w*\s*\([^)]*"{esc}"',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='GPIO-CONSUMER', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['gpio', 'consumer'])

    for f, l, snip in rg_find(rf'{esc}-gpios?\s*=',
                              globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='GPIO-DT', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['gpio', 'dt'])

    for f, l, snip in rg_find(rf'\.label\s*=\s*"{esc}"',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='GPIO-CHIP', file=f, line=l, snippet=snip),
               confidence='low', source='static-rg', tags=['gpio', 'chip'])


# ── IRQ ──
def trace_irq(e: Emitter, root: Path, name: str):
    esc = re.escape(name)

    for f, l, snip in rg_find(
            rf'(devm_)?request_(threaded_)?irq\s*\([^)]*"{esc}"',
            globs=['*.c'], root=root):
        e.emit(Finding(tag='IRQ-REQUEST', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['irq'])

    for f, l, snip in rg_find(rf'platform_get_irq_byname\s*\([^)]*"{esc}"',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='IRQ-GET', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['irq'])

    for f, l, snip in rg_find(rf'interrupt-names\s*=\s*[^;]*"{esc}"',
                              globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='IRQ-DT-NAME', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['irq', 'dt'])

    for f, l, snip in rg_find(rf'irq_domain_add_\w+\s*\([^)]*"{esc}"',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='IRQ-DOMAIN', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['irq', 'domain'])


# ── power-domain ──
def trace_power_domain(e: Emitter, root: Path, name: str):
    esc = re.escape(name)

    for f, l, snip in rg_find(rf'\.name\s*=\s*"{esc}"',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='PD-PROVIDER', file=f, line=l, snippet=snip),
               confidence='low', source='static-rg', tags=['power-domain'])

    for f, l, snip in rg_find(rf'pm_genpd_init\s*\([^)]*"{esc}"',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='PD-INIT', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['power-domain'])

    for f, l, snip in rg_find(r'of_genpd_add_provider\w*\s*\(',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='PD-OF-PROVIDER', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['power-domain'])

    for f, l, snip in rg_find(rf'power-domain-names\s*=\s*[^;]*"{esc}"',
                              globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='PD-DT-NAME', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['power-domain', 'dt'])

    for f, l, snip in rg_find(
            rf'dev_pm_domain_attach_by_name\s*\([^)]*"{esc}"',
            globs=['*.c'], root=root):
        e.emit(Finding(tag='PD-CONSUMER', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['power-domain', 'consumer'])


def do_scan(e: Emitter, root: Path, out_path: Optional[Path]):
    lines = []

    for f, l, snip in rg_find(
            r'(devm_)?clk_get(_optional)?\s*\([^,]+,\s*"([^"]+)"',
            globs=['*.c'], root=root, timeout=300):
        cm = re.search(r'"([^"]+)"\s*\)', snip)
        if cm:
            lines.append(f'CLK-CONSUMER\t{f}:{l}\t{cm.group(1)}')

    for f, l, snip in rg_find(
            r'(devm_)?regulator_get(_optional)?\s*\([^,]+,\s*"([^"]+)"',
            globs=['*.c'], root=root, timeout=300):
        cm = re.search(r'"([^"]+)"\s*\)', snip)
        if cm:
            lines.append(f'REG-CONSUMER\t{f}:{l}\t{cm.group(1)}')

    for f, l, snip in rg_find(
            r'(devm_)?gpiod_get(_optional|_index)?\s*\([^,]+,\s*"([^"]+)"',
            globs=['*.c'], root=root, timeout=300):
        cm = re.search(r'"([^"]+)"', snip)
        if cm:
            lines.append(f'GPIO-CONSUMER\t{f}:{l}\t{cm.group(1)}')

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('Kernel 子系统资源框架追踪')
    p.add_argument('--clock', help='clock name（如 "xclk"）')
    p.add_argument('--regulator', help='regulator supply name（如 "vdd"）')
    p.add_argument('--gpio', help='GPIO label（如 "reset"）')
    p.add_argument('--irq', help='IRQ name/label（如 "vblank"）')
    p.add_argument('--power-domain', dest='power_domain',
                   help='power domain name（如 "gpu"）')
    p.add_argument('--scan', action='store_true', help='全量扫描所有 consumer')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.clock:
            trace_clock(e, search_root, args.clock)
        elif args.regulator:
            trace_regulator(e, search_root, args.regulator)
        elif args.gpio:
            trace_gpio(e, search_root, args.gpio)
        elif args.irq:
            trace_irq(e, search_root, args.irq)
        elif args.power_domain:
            trace_power_domain(e, search_root, args.power_domain)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
