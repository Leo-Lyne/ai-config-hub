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

GKI 支持：自动探测多个 kernel root（kernel/common、kernel-5.x、kernel/<vendor>* 等），
对每个 root 分别跑一次搜索。

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


def _kernel_roots(bsp_root: Path) -> list[Path]:
    """返回所有存在的 kernel root 候选。
    GKI / vendor BSP 常见布局：
      - kernel/common            (GKI 公共内核)
      - kernel/private           (vendor 私有内核)
      - kernel-5.10 / kernel-5.15 / kernel-6.1 / kernel-6.6
      - kernel/<vendor>*         (kernel/qcom、kernel/mediatek)
      - vendor/<vendor>*/kernel* (vendor/qcom-opensource/kernel 等)
      - kernel/                  (扁平布局，兜底)
    """
    candidates: list[Path] = []

    # 具名候选
    for sub in ('kernel/common', 'kernel/private'):
        p = bsp_root / sub
        if p.exists():
            candidates.append(p)

    # kernel-<ver>
    for p in sorted(bsp_root.glob('kernel-[0-9]*')):
        if p.is_dir():
            candidates.append(p)

    # kernel/<vendor>*
    kdir = bsp_root / 'kernel'
    if kdir.is_dir():
        for p in sorted(kdir.glob('*')):
            if p.is_dir() and p.name not in ('common', 'private'):
                # 只取看起来像 vendor 目录的（不是 tests / scripts）
                if not p.name.startswith('.'):
                    candidates.append(p)

    # vendor/<vendor>*/kernel*
    vdir = bsp_root / 'vendor'
    if vdir.is_dir():
        for p in sorted(vdir.glob('*/kernel*')):
            if p.is_dir():
                candidates.append(p)

    # 兜底：kernel/ 本身
    if kdir.exists() and kdir not in candidates:
        # 只有当没有更具体的候选时才加
        if not candidates:
            candidates.append(kdir)

    # 去重保序
    seen = set()
    result = []
    for p in candidates:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            result.append(p)
    return result


def trace_config(e: Emitter, search_root: Path, roots: list[Path], symbol: str):
    """追踪单个 CONFIG 符号的完整链路（对每个 kernel root 跑一遍）。"""
    bare = symbol.removeprefix('CONFIG_')
    full = f'CONFIG_{bare}'

    # 若探测到 kernel roots，则每个 root 都跑一遍；否则退化到 search_root
    targets = roots if roots else [search_root]

    for root in targets:
        # 1. defconfig 设值
        for f, l, snip in rg_find(rf'^{re.escape(full)}[=\s]',
                                  globs=['*defconfig*', '*_defconfig'], root=root):
            e.emit(Finding(tag='DEFCONFIG', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['kconfig'])

        # .config
        for f, l, snip in rg_find(rf'^{re.escape(full)}[=\s]',
                                  globs=['.config'], root=root):
            e.emit(Finding(tag='DOT-CONFIG', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['kconfig'])

        # 2. Kconfig 定义
        for f, l, snip in rg_find(rf'^\s*config\s+{re.escape(bare)}\s*$',
                                  globs=['Kconfig', 'Kconfig.*'], root=root):
            e.emit(Finding(tag='KCONFIG-DEF', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['kconfig'])

        for f, l, snip in rg_find(rf'^\s*menuconfig\s+{re.escape(bare)}\s*$',
                                  globs=['Kconfig', 'Kconfig.*'], root=root):
            e.emit(Finding(tag='KCONFIG-DEF', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['kconfig'])

        for f, l, snip in rg_find(
                rf'(depends on|select|imply)\s+.*\b{re.escape(bare)}\b',
                globs=['Kconfig', 'Kconfig.*'], root=root):
            e.emit(Finding(tag='KCONFIG-REF', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg', tags=['kconfig'])

        # 3. Makefile
        for f, l, snip in rg_find(rf'obj-\$\({re.escape(full)}\)',
                                  globs=['Makefile', 'Makefile.*', '*.mk'],
                                  root=root):
            e.emit(Finding(tag='MAKEFILE-OBJ', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['kconfig', 'makefile'])

        for f, l, snip in rg_find(rf'\$\({re.escape(full)}\)',
                                  globs=['Makefile', 'Makefile.*'], root=root):
            if 'obj-' not in snip:
                e.emit(Finding(tag='MAKEFILE-REF', file=f, line=l, snippet=snip),
                       confidence='med', source='static-rg', tags=['kconfig', 'makefile'])

        # 4. C 源码
        for f, l, snip in rg_find(rf'#\s*if.*\b{re.escape(full)}\b',
                                  globs=['*.c', '*.h'], root=root):
            e.emit(Finding(tag='CODE-IFDEF', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['kconfig', 'code'])

        for f, l, snip in rg_find(rf'IS_ENABLED\s*\(\s*{re.escape(full)}\s*\)',
                                  globs=['*.c', '*.h'], root=root):
            e.emit(Finding(tag='CODE-IS-ENABLED', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['kconfig', 'code'])

        for f, l, snip in rg_find(rf'IS_BUILTIN\s*\(\s*{re.escape(full)}\s*\)',
                                  globs=['*.c', '*.h'], root=root):
            e.emit(Finding(tag='CODE-IS-BUILTIN', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['kconfig', 'code'])

        for f, l, snip in rg_find(rf'IS_MODULE\s*\(\s*{re.escape(full)}\s*\)',
                                  globs=['*.c', '*.h'], root=root):
            e.emit(Finding(tag='CODE-IS-MODULE', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg', tags=['kconfig', 'code'])


def do_scan(e: Emitter, search_root: Path, defconfig: Optional[str],
            out_path: Optional[Path]):
    lines = []
    if defconfig:
        dc_path = search_root / defconfig if not Path(defconfig).is_absolute() \
            else Path(defconfig)
    else:
        candidates = list(search_root.glob('arch/*/configs/*_defconfig'))
        if not candidates:
            # 也尝试 kernel root 下
            for kr in _kernel_roots(search_root):
                candidates.extend(kr.glob('arch/*/configs/*_defconfig'))
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
            e.emit(Finding(tag='DEFCONFIG', file=str(dc_path), line=0,
                           snippet=f'{m.group(1)}={m.group(2)}'),
                   confidence='high', source='static-rg', tags=['kconfig', 'scan'])

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('Kconfig ↔ 代码追踪（GKI 多 kernel root）')
    p.add_argument('--config', '-c',
                   help='CONFIG 符号（如 CONFIG_VIDEO_IMX219 或 VIDEO_IMX219）')
    p.add_argument('--scan', action='store_true',
                   help='扫描 defconfig 所有 =y/=m')
    p.add_argument('--defconfig', help='defconfig 路径（--scan 时用）')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()
    roots = _kernel_roots(search_root)
    if roots:
        print(f'# kernel roots: {[str(r) for r in roots]}', file=sys.stderr)

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.defconfig, args.out)
        elif args.config:
            trace_config(e, search_root, roots, args.config)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
