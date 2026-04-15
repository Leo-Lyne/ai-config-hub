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

识别链路（以 clock 为例）：
  1. Provider:  clk_hw_register_* / CLK_OF_DECLARE / .clk_init_cb
  2. Consumer:  clk_get(dev, "name") / devm_clk_get / of_clk_get_by_name
  3. DT:        clocks = <&phandle N>; clock-names = "name";

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
    """执行 rg 搜索，返回 (file, line, content) 三元组列表。"""
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


# ── clock ──

def trace_clock(root: Path, name: str):
    esc = re.escape(name)

    # Consumer: clk_get / devm_clk_get / devm_clk_get_optional / of_clk_get_by_name
    for f, l, c in _rg(root, rf'(devm_)?clk_get(_optional)?\s*\([^)]*"{esc}"',
                        ['*.c', '*.h']):
        emit('CLK-CONSUMER', f'{f}:{l}', c)

    for f, l, c in _rg(root, rf'of_clk_get_by_name\s*\([^)]*"{esc}"',
                        ['*.c', '*.h']):
        emit('CLK-CONSUMER', f'{f}:{l}', c)

    # DT: clock-names 里出现该名字
    for f, l, c in _rg(root, rf'clock-names\s*=\s*[^;]*"{esc}"',
                        ['*.dts', '*.dtsi']):
        emit('CLK-DT-NAME', f'{f}:{l}', c)

    # Provider: clk_hw_register_fixed_rate / clk_register_* 里引用该名字
    for f, l, c in _rg(root, rf'clk_(hw_)?register\w*\s*\([^)]*"{esc}"',
                        ['*.c']):
        emit('CLK-PROVIDER', f'{f}:{l}', c)

    # CLK_OF_DECLARE / CLK_OF_DECLARE_DRIVER
    for f, l, c in _rg(root, rf'CLK_OF_DECLARE\w*\s*\([^)]*"{esc}"',
                        ['*.c', '*.h']):
        emit('CLK-OF-DECLARE', f'{f}:{l}', c)


# ── regulator ──

def trace_regulator(root: Path, name: str):
    esc = re.escape(name)

    # Consumer: regulator_get / devm_regulator_get
    for f, l, c in _rg(root, rf'(devm_)?regulator_get(_optional)?\s*\([^)]*"{esc}"',
                        ['*.c', '*.h']):
        emit('REG-CONSUMER', f'{f}:{l}', c)

    # Provider: regulator_desc.name / .supply_name
    for f, l, c in _rg(root, rf'\.(name|supply_name)\s*=\s*"{esc}"',
                        ['*.c']):
        emit('REG-PROVIDER', f'{f}:{l}', c)

    # regulator_register_*
    for f, l, c in _rg(root, rf'(devm_)?regulator_register\s*\(',
                        ['*.c']):
        # 在同文件里找引用该 name 的 regulator_desc
        pass  # 上面的 .name 搜索已覆盖

    # DT: xxx-supply = <&phandle>
    for f, l, c in _rg(root, rf'{esc}-supply\s*=',
                        ['*.dts', '*.dtsi']):
        emit('REG-DT-SUPPLY', f'{f}:{l}', c)

    # regulator 节点: regulator-name = "xxx"
    for f, l, c in _rg(root, rf'regulator-name\s*=\s*"{esc}"',
                        ['*.dts', '*.dtsi']):
        emit('REG-DT-NAME', f'{f}:{l}', c)


# ── GPIO ──

def trace_gpio(root: Path, name: str):
    esc = re.escape(name)

    # Consumer: devm_gpiod_get / gpiod_get / devm_gpio_get_*
    for f, l, c in _rg(root, rf'(devm_)?gpiod_get(_optional|_index)?\s*\([^)]*"{esc}"',
                        ['*.c', '*.h']):
        emit('GPIO-CONSUMER', f'{f}:{l}', c)

    # of_get_named_gpio / of_get_gpio
    for f, l, c in _rg(root, rf'of_get_named_gpio\w*\s*\([^)]*"{esc}"',
                        ['*.c']):
        emit('GPIO-CONSUMER', f'{f}:{l}', c)

    # DT: xxx-gpios / xxx-gpio
    for f, l, c in _rg(root, rf'{esc}-gpios?\s*=',
                        ['*.dts', '*.dtsi']):
        emit('GPIO-DT', f'{f}:{l}', c)

    # Provider: gpio_chip.label
    for f, l, c in _rg(root, rf'\.label\s*=\s*"{esc}"',
                        ['*.c']):
        emit('GPIO-CHIP', f'{f}:{l}', c)


# ── IRQ ──

def trace_irq(root: Path, name: str):
    esc = re.escape(name)

    # request_irq / devm_request_irq（第 4 个参数是 name）
    for f, l, c in _rg(root, rf'(devm_)?request_(threaded_)?irq\s*\([^)]*"{esc}"',
                        ['*.c']):
        emit('IRQ-REQUEST', f'{f}:{l}', c)

    # platform_get_irq_byname
    for f, l, c in _rg(root, rf'platform_get_irq_byname\s*\([^)]*"{esc}"',
                        ['*.c']):
        emit('IRQ-GET', f'{f}:{l}', c)

    # DT: interrupt-names
    for f, l, c in _rg(root, rf'interrupt-names\s*=\s*[^;]*"{esc}"',
                        ['*.dts', '*.dtsi']):
        emit('IRQ-DT-NAME', f'{f}:{l}', c)

    # irq_chip.name
    for f, l, c in _rg(root, rf'\.name\s*=\s*"{esc}"',
                        ['*.c']):
        # 只取附近有 irq_chip 的
        pass  # 太泛了，跳过——用 IRQ-REQUEST 已够

    # irq_domain_add_*
    for f, l, c in _rg(root, rf'irq_domain_add_\w+\s*\([^)]*"{esc}"',
                        ['*.c']):
        emit('IRQ-DOMAIN', f'{f}:{l}', c)


# ── power-domain ──

def trace_power_domain(root: Path, name: str):
    esc = re.escape(name)

    # genpd name / pm_genpd_init
    for f, l, c in _rg(root, rf'\.name\s*=\s*"{esc}"',
                        ['*.c']):
        emit('PD-PROVIDER', f'{f}:{l}', c)

    for f, l, c in _rg(root, rf'pm_genpd_init\s*\([^)]*"{esc}"',
                        ['*.c']):
        emit('PD-INIT', f'{f}:{l}', c)

    # of_genpd_add_provider_*
    for f, l, c in _rg(root, rf'of_genpd_add_provider\w*\s*\(',
                        ['*.c']):
        emit('PD-OF-PROVIDER', f'{f}:{l}', c)

    # DT: power-domain-names
    for f, l, c in _rg(root, rf'power-domain-names\s*=\s*[^;]*"{esc}"',
                        ['*.dts', '*.dtsi']):
        emit('PD-DT-NAME', f'{f}:{l}', c)

    # Consumer: dev_pm_domain_attach_by_name
    for f, l, c in _rg(root, rf'dev_pm_domain_attach_by_name\s*\([^)]*"{esc}"',
                        ['*.c']):
        emit('PD-CONSUMER', f'{f}:{l}', c)


# ── scan ──

def do_scan(root: Path, out_path: Optional[Path]):
    lines = []

    # clocks
    for f, l, c in _rg(root, r'(devm_)?clk_get(_optional)?\s*\([^,]+,\s*"([^"]+)"',
                        ['*.c'], timeout=300):
        cm = re.search(r'"([^"]+)"\s*\)', c)
        if cm:
            lines.append(f'CLK-CONSUMER\t{f}:{l}\t{cm.group(1)}')

    # regulators
    for f, l, c in _rg(root, r'(devm_)?regulator_get(_optional)?\s*\([^,]+,\s*"([^"]+)"',
                        ['*.c'], timeout=300):
        cm = re.search(r'"([^"]+)"\s*\)', c)
        if cm:
            lines.append(f'REG-CONSUMER\t{f}:{l}\t{cm.group(1)}')

    # GPIOs
    for f, l, c in _rg(root, r'(devm_)?gpiod_get(_optional|_index)?\s*\([^,]+,\s*"([^"]+)"',
                        ['*.c'], timeout=300):
        cm = re.search(r'"([^"]+)"', c)
        if cm:
            lines.append(f'GPIO-CONSUMER\t{f}:{l}\t{cm.group(1)}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='Kernel 子系统资源框架追踪')
    ap.add_argument('--clock', help='clock name（如 "xclk"）')
    ap.add_argument('--regulator', help='regulator supply name（如 "vdd"）')
    ap.add_argument('--gpio', help='GPIO label（如 "reset"）')
    ap.add_argument('--irq', help='IRQ name/label（如 "vblank"）')
    ap.add_argument('--power-domain', help='power domain name（如 "gpu"）')
    ap.add_argument('--scan', action='store_true', help='全量扫描所有 consumer')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.clock:
        trace_clock(args.root, args.clock)
    elif args.regulator:
        trace_regulator(args.root, args.regulator)
    elif args.gpio:
        trace_gpio(args.root, args.gpio)
    elif args.irq:
        trace_irq(args.root, args.irq)
    elif args.power_domain:
        trace_power_domain(args.root, args.power_domain)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
