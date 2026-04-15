#!/usr/bin/env python3
"""
Android Build 系统追踪：模块定义 → 安装路径 + VNDK 可见性。

用法：
  build_trace.py --module camera.provider
  build_trace.py --so libcamera_provider.so
  build_trace.py --vndk libutils
  build_trace.py --scan [--out .build.idx]

Task 11 扩展：多分区安装路径推断
  用 scan_partitions 探测 lib / lib64 / bin / etc，给出模块可能实际
  落盘的分区+路径清单（system、vendor、odm、system_ext、product）。

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


# 模块类型 → 候选子路径
_INSTALL_SUBPATHS = {
    'cc_binary': ['bin'],
    'cc_library_shared': ['lib', 'lib64'],
    'cc_library_static': [],
    'java_library': ['framework'],
    'android_app': ['app', 'priv-app'],
    'cc_binary_host': [],
    'hal_service': ['bin/hw'],
    'prebuilt_etc': ['etc'],
}


def _partition_install_hints(bsp_root: Path, mtype: str, module_name: str) -> list[str]:
    """返回模块可能实际落盘的 <part>/<subpath>/<name> 路径列表。"""
    hints: list[str] = []
    subs = _INSTALL_SUBPATHS.get(mtype, [])
    if not subs:
        return hints
    for sub in subs:
        for part_dir in scan_partitions(bsp_root, sub):
            # 只检查下面是否有 module_name 匹配的文件
            for name_variant in (module_name, f'lib{module_name}.so',
                                 f'{module_name}.so', f'{module_name}.apk'):
                cand = part_dir / name_variant
                if cand.exists():
                    hints.append(str(cand))
    return hints


def trace_module(e: Emitter, bsp_root: Path, name: str):
    esc = re.escape(name)

    for f, l, snip in rg_find(rf'name\s*:\s*"{esc}"',
                              globs=['Android.bp'], root=bsp_root):
        e.emit(Finding(tag='BP-MODULE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['build', 'bp'])
        _find_module_type(e, bsp_root, f, int(l), name)

    for f, l, snip in rg_find(rf'LOCAL_MODULE\s*:?=\s*{esc}\s*$',
                              globs=['Android.mk'], root=bsp_root):
        e.emit(Finding(tag='MK-MODULE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['build', 'mk'])

    for f, l, snip in rg_find(rf'LOCAL_MODULE_(RELATIVE_)?PATH\s*:?=',
                              globs=['Android.mk'], root=bsp_root):
        if name in f or name.replace('.', '_') in f:
            e.emit(Finding(tag='MK-INSTALL-PATH', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg',
                   tags=['build', 'install'])

    for f, l, snip in rg_find(rf'PRODUCT_PACKAGES\s*\+?=.*\b{esc}\b',
                              globs=['*.mk'], root=bsp_root):
        e.emit(Finding(tag='PRODUCT-PKG', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['build', 'product'])

    for f, l, snip in rg_find(
            rf'(shared_libs|static_libs|required)\s*:.*"{esc}"',
            globs=['Android.bp'], root=bsp_root):
        e.emit(Finding(tag='BP-DEP', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['build', 'dep'])

    _check_vndk(e, bsp_root, name)


def _find_module_type(e: Emitter, bsp_root: Path, fpath: str,
                      name_line: int, module_name: str):
    try:
        lines = Path(fpath).read_text().splitlines()
    except OSError:
        return
    for i in range(name_line - 1, max(name_line - 20, -1), -1):
        if i < 0 or i >= len(lines):
            continue
        m = re.match(r'^(\w+)\s*\{', lines[i])
        if m:
            mtype = m.group(1)
            hints = _partition_install_hints(bsp_root, mtype, module_name)
            desc = mtype
            if hints:
                desc = f'{mtype}  →  {"; ".join(hints)}'
            e.emit(Finding(tag='BP-TYPE', file=fpath, line=i + 1,
                           snippet=desc),
                   confidence='med', source='static-rg',
                   tags=['build', 'install'])
            return


def _check_vndk(e: Emitter, bsp_root: Path, name: str):
    for f, l, snip in rg_find(r'vendor_available\s*:\s*true',
                              globs=['Android.bp'], root=bsp_root):
        if _is_same_module_block(f, int(l), name):
            e.emit(Finding(tag='VNDK-VENDOR', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg',
                   tags=['build', 'vndk'])

    for f, l, snip in rg_find(r'vndk\s*:\s*\{',
                              globs=['Android.bp'], root=bsp_root):
        if _is_same_module_block(f, int(l), name):
            e.emit(Finding(tag='VNDK-ENABLED', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg',
                   tags=['build', 'vndk'])


def _is_same_module_block(fpath: str, attr_line: int, module_name: str) -> bool:
    try:
        lines = Path(fpath).read_text().splitlines()
    except OSError:
        return False
    search_range = range(max(0, attr_line - 50),
                         min(len(lines), attr_line + 50))
    for i in search_range:
        if f'"{module_name}"' in lines[i]:
            return True
    return False


def trace_so(e: Emitter, bsp_root: Path, so_name: str):
    bare = so_name
    if bare.startswith('lib'):
        bare = bare[3:]
    if bare.endswith('.so'):
        bare = bare[:-3]

    esc_so = re.escape(so_name)
    esc_bare = re.escape(bare)

    for f, l, snip in rg_find(rf'name\s*:\s*"(lib)?{esc_bare}"',
                              globs=['Android.bp'], root=bsp_root):
        e.emit(Finding(tag='BP-MODULE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['build', 'bp'])

    for f, l, snip in rg_find(rf'stem\s*:\s*"{esc_bare}"',
                              globs=['Android.bp'], root=bsp_root):
        e.emit(Finding(tag='BP-STEM', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['build', 'bp'])

    for f, l, snip in rg_find(rf'LOCAL_MODULE\s*:?=\s*(lib)?{esc_bare}\s*$',
                              globs=['Android.mk'], root=bsp_root):
        e.emit(Finding(tag='MK-MODULE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['build', 'mk'])

    for f, l, snip in rg_find(rf'"{esc_so}"|"lib{esc_bare}"',
                              globs=['Android.bp', 'Android.mk'], root=bsp_root):
        e.emit(Finding(tag='BUILD-REF', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['build', 'ref'])

    # 多分区落盘探测
    for sub in ('lib', 'lib64'):
        for part_dir in scan_partitions(bsp_root, sub):
            cand = part_dir / so_name
            if cand.exists():
                e.emit(Finding(tag='INSTALL-PATH', file=str(cand), line=0,
                               snippet=f'installed .so ({sub})'),
                       confidence='high', source='static-fs',
                       tags=['build', 'install'])


def trace_vndk(e: Emitter, bsp_root: Path, name: str):
    trace_module(e, bsp_root, name)


def do_scan(e: Emitter, bsp_root: Path, out_path: Optional[Path]):
    lines = []

    for f, l, snip in rg_find(r'name\s*:\s*"([^"]+)"',
                              globs=['Android.bp'], root=bsp_root, timeout=300):
        cm = re.search(r'"([^"]+)"', snip)
        if cm:
            lines.append(f'BP-MODULE\t{f}:{l}\t{cm.group(1)}')

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('Android Build 系统追踪（多分区）')
    p.add_argument('--module', '-m', help='模块名')
    p.add_argument('--so', help='.so 文件名')
    p.add_argument('--vndk', help='检查 VNDK 可见性')
    p.add_argument('--scan', action='store_true', help='全量扫描所有模块')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.module:
            trace_module(e, search_root, args.module)
        elif args.so:
            trace_so(e, search_root, args.so)
        elif args.vndk:
            trace_vndk(e, search_root, args.vndk)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
