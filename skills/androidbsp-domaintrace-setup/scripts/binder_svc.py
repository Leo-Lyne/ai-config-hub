#!/usr/bin/env python3
"""
Binder service 注册 ↔ 进程 ↔ VINTF manifest 追踪。

用法：
  binder_svc.py --service "camera.provider"
  binder_svc.py --service ICameraProvider
  binder_svc.py --process cameraserver
  binder_svc.py --hal android.hardware.camera.provider
  binder_svc.py --scan [--out .binder_svc.idx]

Task 11 扩展：
  - 多分区 VINTF manifest：扫描 system / vendor / odm / system_ext / product
    每个分区的 etc/vintf/manifest.xml + manifest/*.xml
  - 多候选 compat matrix：compatibility_matrix.<level>.xml（如 30 / 31 / 32 / 33 / 34）

依赖：rg。
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Finding, Emitter, make_parser, rg_find, scan_partitions, first_existing,
    require_version,
)

require_version("1.0.0")


VINTF_GLOBS = ['manifest*.xml', 'compatibility_matrix*.xml',
               'vintf/*.xml', 'manifest/*.xml']


def _vintf_roots(bsp_root: Path) -> list[Path]:
    """返回多分区 VINTF 搜索根。"""
    roots: list[Path] = []
    for part_vintf in scan_partitions(bsp_root, 'etc/vintf'):
        roots.append(part_vintf)
    # 顶层：hardware/interfaces、device、vendor（源码树）
    for sub in ('hardware/interfaces', 'device', 'vendor'):
        p = bsp_root / sub
        if p.exists():
            roots.append(p)
    if not roots:
        roots.append(bsp_root)
    return roots


def _compat_matrix_candidates(bsp_root: Path) -> list[Path]:
    """探测 compatibility_matrix.<level>.xml 多候选。"""
    found = []
    for part_vintf in scan_partitions(bsp_root, 'etc/vintf'):
        for level in ('29', '30', '31', '32', '33', '34', '35'):
            cand = part_vintf / f'compatibility_matrix.{level}.xml'
            if cand.exists():
                found.append(cand)
        # 不带 level 的
        cand = part_vintf / 'compatibility_matrix.xml'
        if cand.exists():
            found.append(cand)
    return found


def trace_service(e: Emitter, bsp_root: Path, svc_name: str):
    esc = re.escape(svc_name)

    # C++/Java addService
    for globs, langtag in (
            (['*.cpp', '*.cc', '*.h'], 'cpp'),
            (['*.java'], 'java')):
        for f, l, snip in rg_find(rf'addService\s*\([^)]*"{esc}"',
                                  globs=globs, root=bsp_root):
            e.emit(Finding(tag='SVC-REGISTER', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['binder', 'register', langtag])

    # getService
    for globs, langtag in (
            (['*.cpp', '*.cc', '*.h'], 'cpp'),
            (['*.java'], 'java')):
        for f, l, snip in rg_find(rf'getService\s*\([^)]*"{esc}"',
                                  globs=globs, root=bsp_root):
            e.emit(Finding(tag='SVC-GET', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['binder', 'get', langtag])

    if svc_name.startswith('I'):
        for f, l, snip in rg_find(rf'{esc}::getService\s*\(',
                                  globs=['*.cpp', '*.cc', '*.h'], root=bsp_root):
            e.emit(Finding(tag='SVC-GET', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['binder', 'hidl'])

    # .rc: service 名
    for f, l, snip in rg_find(rf'^\s*service\s+{esc}\s',
                              globs=['*.rc'], root=bsp_root):
        e.emit(Finding(tag='RC-SERVICE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['binder', 'rc'])

    for f, l, snip in rg_find(rf'^\s*interface\s+\w+\s+{esc}',
                              globs=['*.rc'], root=bsp_root):
        e.emit(Finding(tag='RC-INTERFACE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['binder', 'rc'])

    # VINTF（多分区）
    _search_vintf(e, bsp_root, svc_name)

    # service_contexts
    for f, l, snip in rg_find(rf'^{esc}\s',
                              globs=['*service_contexts*'], root=bsp_root):
        e.emit(Finding(tag='SVC-CONTEXT', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg',
               tags=['binder', 'selinux'])


def trace_process(e: Emitter, bsp_root: Path, proc_name: str):
    esc = re.escape(proc_name)

    rc_files = set()
    for f, l, snip in rg_find(rf'^\s*service\s+{esc}\s',
                              globs=['*.rc'], root=bsp_root):
        e.emit(Finding(tag='RC-SERVICE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['binder', 'rc'])
        rc_files.add(f)

    for rc in rc_files:
        for f, l, snip in rg_find(r'^\s*interface\s+', root=Path(rc)):
            e.emit(Finding(tag='RC-INTERFACE', file=rc, line=l, snippet=snip),
                   confidence='med', source='static-rg',
                   tags=['binder', 'rc'])

    # Android.bp
    for f, l, snip in rg_find(rf'name\s*:\s*"{esc}"',
                              globs=['Android.bp'], root=bsp_root):
        e.emit(Finding(tag='BUILD-DEF', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['binder', 'build'])

    # VINTF（多分区）
    for root in _vintf_roots(bsp_root):
        for f, l, snip in rg_find(rf'{esc}',
                                  globs=VINTF_GLOBS, root=root):
            e.emit(Finding(tag='VINTF', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg',
                   tags=['binder', 'vintf'])


def trace_hal(e: Emitter, bsp_root: Path, hal_name: str):
    esc = re.escape(hal_name)

    for root in _vintf_roots(bsp_root):
        for f, l, snip in rg_find(rf'{esc}', globs=VINTF_GLOBS, root=root):
            e.emit(Finding(tag='VINTF', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['binder', 'vintf'])

    # 多候选 compat matrix
    for matrix in _compat_matrix_candidates(bsp_root):
        for f, l, snip in rg_find(rf'{esc}', root=matrix):
            e.emit(Finding(tag='VINTF-MATRIX', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['binder', 'vintf', 'matrix'])

    for f, l, snip in rg_find(rf'interface\s+\w+\s+{esc}',
                              globs=['*.rc'], root=bsp_root):
        e.emit(Finding(tag='RC-INTERFACE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['binder', 'rc'])

    for f, l, snip in rg_find(rf'package\s+{esc}',
                              globs=['*.aidl'], root=bsp_root):
        e.emit(Finding(tag='AIDL-PACKAGE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['binder', 'aidl'])

    for f, l, snip in rg_find(rf'"{esc}[/"]',
                              globs=['*.cpp', '*.cc'], root=bsp_root):
        e.emit(Finding(tag='HAL-REF', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['binder', 'hal'])


def _search_vintf(e: Emitter, bsp_root: Path, keyword: str):
    esc = re.escape(keyword)
    for root in _vintf_roots(bsp_root):
        for f, l, snip in rg_find(esc, globs=VINTF_GLOBS, root=root):
            e.emit(Finding(tag='VINTF', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg',
                   tags=['binder', 'vintf'])


def do_scan(e: Emitter, bsp_root: Path, out_path: Optional[Path]):
    lines = []

    for root in _vintf_roots(bsp_root):
        for f, l, snip in rg_find(
                r'<name>([\w.]+)</name>',
                globs=['manifest*.xml', 'vintf/*.xml'], root=root, timeout=120):
            cm = re.search(r'<name>([\w.]+)</name>', snip)
            if cm:
                lines.append(f'VINTF-HAL\t{f}:{l}\t{cm.group(1)}')
                e.emit(Finding(tag='VINTF-HAL', file=f, line=l,
                               snippet=cm.group(1)),
                       confidence='high', source='static-rg',
                       tags=['binder', 'vintf', 'scan'])

    for f, l, snip in rg_find(r'^\s*service\s+(\w+)\s',
                              globs=['*.rc'], root=bsp_root, timeout=120):
        cm = re.search(r'service\s+(\w+)', snip)
        if cm:
            lines.append(f'RC-SERVICE\t{f}:{l}\t{cm.group(1)}')
            e.emit(Finding(tag='RC-SERVICE', file=f, line=l,
                           snippet=cm.group(1)),
                   confidence='high', source='static-rg',
                   tags=['binder', 'rc', 'scan'])

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('Binder service ↔ 进程 ↔ VINTF 追踪（多分区）')
    p.add_argument('--service', '-s', help='service 名')
    p.add_argument('--process', '-p', help='.rc 进程名')
    p.add_argument('--hal', help='VINTF HAL FQDN')
    p.add_argument('--scan', action='store_true', help='全量扫描 VINTF manifest')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.service:
            trace_service(e, search_root, args.service)
        elif args.process:
            trace_process(e, search_root, args.process)
        elif args.hal:
            trace_hal(e, search_root, args.hal)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
