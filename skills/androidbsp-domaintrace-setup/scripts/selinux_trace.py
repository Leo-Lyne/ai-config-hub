#!/usr/bin/env python3
"""
SELinux 策略 ↔ 代码/设备节点追踪。

用法：
  selinux_trace.py --avc 'avc: denied { read } ... scontext=...'
  selinux_trace.py --domain hal_camera_default
  selinux_trace.py --device /dev/video0
  selinux_trace.py --type sysfs_camera
  selinux_trace.py --service-context camera.provider
  selinux_trace.py --scan [--out .selinux.idx]

Task 11 扩展：
  - 多分区探测：scan_partitions(bsp_root, 'etc/selinux') 枚举
    system / vendor / odm / system_ext / product 的 sepolicy 根
  - glob 扩展：*.te + *.cil（AOSP 11+ 编译后策略）
  - 识别 Android 11+ mapping/<sdk>.cil（例如 mapping/30.0.cil）

依赖：rg, fd。
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Finding, Emitter, make_parser, rg_find, run_cmd, scan_partitions,
    require_version,
)

require_version("1.0.0")


SEPOLICY_GLOBS = ['*.te', '*.cil']


def _sepolicy_roots(bsp_root: Path) -> list[Path]:
    """返回所有存在的 sepolicy 根：
      - $bsp/system/sepolicy           (AOSP 基线，旧路径)
      - $bsp/<part>/etc/selinux        (多分区运行时策略，Android 11+)
      - $bsp/device/*/sepolicy         (vendor 源码)
      - $bsp/vendor/*/sepolicy
    """
    roots: list[Path] = []
    for p in (bsp_root / 'system' / 'sepolicy',):
        if p.exists():
            roots.append(p)
    for part_etc in scan_partitions(bsp_root, 'etc/selinux'):
        roots.append(part_etc)
    for p in sorted((bsp_root / 'device').glob('*/sepolicy')) \
            if (bsp_root / 'device').is_dir() else []:
        roots.append(p)
    for p in sorted((bsp_root / 'vendor').glob('*/sepolicy')) \
            if (bsp_root / 'vendor').is_dir() else []:
        roots.append(p)

    if not roots:
        roots.append(bsp_root)
    # 去重
    seen = set()
    out = []
    for p in roots:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def parse_avc(avc_line: str):
    result = {}
    m = re.search(r'\{\s*([^}]+)\}', avc_line)
    if m:
        result['permissions'] = m.group(1).strip().split()
    m = re.search(r'scontext=\S*:(\w+):(\w+):', avc_line)
    if m:
        result['stype'] = m.group(2)
    m = re.search(r'tcontext=\S*:(\w+):(\w+):', avc_line)
    if m:
        result['ttype'] = m.group(2)
    m = re.search(r'tclass=(\w+)', avc_line)
    if m:
        result['tclass'] = m.group(1)
    m = re.search(r'comm="([^"]+)"', avc_line)
    if m:
        result['comm'] = m.group(1)
    m = re.search(r'name="([^"]+)"', avc_line)
    if m:
        result['name'] = m.group(1)
    m = re.search(r'path="([^"]+)"', avc_line)
    if m:
        result['path'] = m.group(1)
    return result


def trace_avc(e: Emitter, bsp_root: Path, avc_line: str):
    info = parse_avc(avc_line)
    if not info:
        print('# 无法解析 avc 日志行', file=sys.stderr)
        return

    perms = info.get('permissions', [])
    stype = info.get('stype', '')
    ttype = info.get('ttype', '')
    tclass = info.get('tclass', '')

    print(f'# AVC 解析: source={stype} target={ttype} class={tclass} '
          f'perms={",".join(perms)}', file=sys.stderr)
    if info.get('path'):
        print(f'# path={info["path"]}', file=sys.stderr)

    if stype and ttype and tclass and perms:
        allow_rule = f'allow {stype} {ttype}:{tclass} {{ {" ".join(perms)} }};'
        e.emit(Finding(tag='SUGGEST-ALLOW', file='-', line=0,
                       snippet=allow_rule),
               confidence='med', source='static-avc',
               tags=['selinux', 'suggest'])

    if stype:
        trace_domain(e, bsp_root, stype)
    if ttype:
        _find_type_def(e, bsp_root, ttype)
    if info.get('path'):
        _find_file_context(e, bsp_root, info['path'])


def _find_file_context(e: Emitter, bsp_root: Path, path: str):
    for root in _sepolicy_roots(bsp_root):
        for f, l, snip in rg_find(rf'{re.escape(path)}',
                                  globs=['*file_contexts*'], root=root):
            e.emit(Finding(tag='FILE-CONTEXT', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'file_contexts'])


def trace_domain(e: Emitter, bsp_root: Path, domain: str):
    esc = re.escape(domain)

    for root in _sepolicy_roots(bsp_root):
        # .te 文件（domain 名通常就是文件名）
        r = run_cmd(['fd', '--type', 'f', rf'^{esc}\.te$', str(root)])
        for line in r.stdout.splitlines():
            if line.strip():
                e.emit(Finding(tag='TE-FILE', file=line.strip(), line=0,
                               snippet=f'policy file for {domain}'),
                       confidence='high', source='static-fd',
                       tags=['selinux', 'te'])

        # type domain, domain; 声明 —— 搜 .te + .cil
        for f, l, snip in rg_find(rf'^\s*type\s+{esc}\s*[,;]',
                                  globs=SEPOLICY_GLOBS, root=root):
            e.emit(Finding(tag='TYPE-DECL', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'type'])

        # allow/neverallow/auditallow/dontaudit 规则（源端）
        for f, l, snip in rg_find(
                rf'^\s*(allow|neverallow|auditallow|dontaudit)\s+{esc}\s',
                globs=SEPOLICY_GLOBS, root=root):
            e.emit(Finding(tag='ALLOW-RULE', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'allow'])

        # 目标端引用
        for f, l, snip in rg_find(
                rf'^\s*(allow|neverallow)\s+\w+\s+{esc}:',
                globs=SEPOLICY_GLOBS, root=root):
            e.emit(Finding(tag='ALLOW-RULE', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'allow'])

        # domain_trans 宏
        for f, l, snip in rg_find(
                rf'(domain_auto_trans|domain_trans|init_daemon_domain)\s*\(\s*{esc}\b',
                globs=SEPOLICY_GLOBS, root=root):
            e.emit(Finding(tag='DOMAIN-TRANS', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'transition'])

        # mapping/<sdk>.cil（Android 11+）
        mapping_dir = root / 'mapping'
        if mapping_dir.is_dir():
            for cil in sorted(mapping_dir.glob('*.cil')):
                for f, l, snip in rg_find(esc, root=cil):
                    e.emit(Finding(tag='CIL-MAPPING', file=str(cil), line=l,
                                   snippet=snip),
                           confidence='med', source='static-rg',
                           tags=['selinux', 'cil', 'mapping'])

    # .rc 文件里的 seclabel（全 bsp）
    for f, l, snip in rg_find(rf'seclabel\s+u:r:{esc}:',
                              globs=['*.rc'], root=bsp_root):
        e.emit(Finding(tag='RC-SECLABEL', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg',
               tags=['selinux', 'rc'])


def trace_device(e: Emitter, bsp_root: Path, dev_path: str):
    esc = re.escape(dev_path)
    types_to_chase = set()

    for root in _sepolicy_roots(bsp_root):
        for f, l, snip in rg_find(rf'{esc}',
                                  globs=['*file_contexts*'], root=root):
            e.emit(Finding(tag='FILE-CONTEXT', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'file_contexts'])
            cm = re.search(r':object_r:(\w+):', snip)
            if cm:
                types_to_chase.add(cm.group(1))

        for f, l, snip in rg_find(rf'{esc}',
                                  globs=['*genfs_contexts*'], root=root):
            e.emit(Finding(tag='GENFS-CONTEXT', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'genfs'])

    for t in types_to_chase:
        _find_allow_for_type(e, bsp_root, t)


def trace_type(e: Emitter, bsp_root: Path, se_type: str):
    _find_type_def(e, bsp_root, se_type)
    _find_allow_for_type(e, bsp_root, se_type)

    for root in _sepolicy_roots(bsp_root):
        for f, l, snip in rg_find(rf'{re.escape(se_type)}',
                                  globs=['*file_contexts*', '*genfs_contexts*',
                                         '*service_contexts*',
                                         '*property_contexts*'],
                                  root=root):
            e.emit(Finding(tag='CONTEXT-REF', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg',
                   tags=['selinux', 'context'])


def trace_service_context(e: Emitter, bsp_root: Path, svc_name: str):
    esc = re.escape(svc_name)

    for root in _sepolicy_roots(bsp_root):
        for f, l, snip in rg_find(rf'^{esc}\s',
                                  globs=['*service_contexts*'], root=root):
            e.emit(Finding(tag='SVC-CONTEXT', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'service'])
            cm = re.search(r':object_r:(\w+):', snip)
            if cm:
                _find_type_def(e, bsp_root, cm.group(1))
                _find_allow_for_type(e, bsp_root, cm.group(1))

        for f, l, snip in rg_find(rf'{esc}',
                                  globs=['*hwservice_contexts*'], root=root):
            e.emit(Finding(tag='HWSVC-CONTEXT', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'hwservice'])

        for f, l, snip in rg_find(rf'{esc}',
                                  globs=['*vndservice_contexts*'], root=root):
            e.emit(Finding(tag='VNDSVC-CONTEXT', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'vndservice'])


def _find_type_def(e: Emitter, bsp_root: Path, se_type: str):
    esc = re.escape(se_type)
    for root in _sepolicy_roots(bsp_root):
        for f, l, snip in rg_find(rf'^\s*type\s+{esc}\s*[,;]',
                                  globs=SEPOLICY_GLOBS, root=root):
            e.emit(Finding(tag='TYPE-DECL', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'type'])


def _find_allow_for_type(e: Emitter, bsp_root: Path, se_type: str):
    esc = re.escape(se_type)
    for root in _sepolicy_roots(bsp_root):
        for f, l, snip in rg_find(
                rf'^\s*(allow|neverallow)\s+\w+\s+{esc}[:\s]',
                globs=SEPOLICY_GLOBS, root=root):
            e.emit(Finding(tag='ALLOW-RULE', file=f, line=l, snippet=snip),
                   confidence='high', source='static-rg',
                   tags=['selinux', 'allow'])


def do_scan(e: Emitter, bsp_root: Path, out_path: Optional[Path]):
    lines = []

    for root in _sepolicy_roots(bsp_root):
        r = run_cmd(['fd', '--type', 'f', r'\.te$', str(root)])
        for fpath in r.stdout.splitlines():
            if fpath.strip():
                lines.append(f'TE-FILE\t{fpath.strip()}')

        r = run_cmd(['fd', '--type', 'f', r'\.cil$', str(root)])
        for fpath in r.stdout.splitlines():
            if fpath.strip():
                lines.append(f'CIL-FILE\t{fpath.strip()}')

        for f, l, snip in rg_find(r'^\s*type\s+(\w+)\s*,',
                                  globs=SEPOLICY_GLOBS, root=root):
            cm = re.search(r'type\s+(\w+)', snip)
            if cm:
                lines.append(f'TYPE-DECL\t{f}:{l}\t{cm.group(1)}')

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('SELinux 策略 ↔ 代码/设备节点追踪（多分区 + CIL）')
    p.add_argument('--avc', help='avc: denied 日志行（整行粘贴）')
    p.add_argument('--domain', help='SELinux domain')
    p.add_argument('--device', help='设备节点路径')
    p.add_argument('--type', dest='se_type', help='SELinux type')
    p.add_argument('--service-context', dest='service_context',
                   help='service 名（查 service_contexts）')
    p.add_argument('--scan', action='store_true',
                   help='全量扫描所有 .te / .cil 文件')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.avc:
            trace_avc(e, search_root, args.avc)
        elif args.domain:
            trace_domain(e, search_root, args.domain)
        elif args.device:
            trace_device(e, search_root, args.device)
        elif args.se_type:
            trace_type(e, search_root, args.se_type)
        elif args.service_context:
            trace_service_context(e, search_root, args.service_context)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
