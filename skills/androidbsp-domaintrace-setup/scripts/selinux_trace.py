#!/usr/bin/env python3
"""
SELinux 策略 ↔ 代码/设备节点追踪。

用法：
  # 解析 avc denied 日志行，定位相关 .te 策略文件
  selinux_trace.py --avc 'avc: denied { read } for ... scontext=u:r:hal_camera_default:s0 tcontext=u:object_r:sysfs:s0 tclass=file'

  # 按 SELinux domain 追踪（找 .te 策略文件 + 对应进程）
  selinux_trace.py --domain hal_camera_default

  # 按设备节点追踪（找 file_contexts label + 哪些 domain 有权限）
  selinux_trace.py --device /dev/video0

  # 按 SELinux type 追踪（找 file_contexts + .te 引用）
  selinux_trace.py --type sysfs_camera

  # 按 service 名找 service_contexts + 对应策略
  selinux_trace.py --service-context camera.provider

  # 全量扫描：列出所有自定义 .te 文件
  selinux_trace.py --scan [--out .selinux.idx]

典型 SELinux 策略文件位置：
  - system/sepolicy/          (AOSP 基线)
  - device/<vendor>/sepolicy/ (vendor 自定义)
  - vendor/<vendor>/sepolicy/

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


def parse_avc(avc_line: str):
    """解析 avc: denied 日志，提取关键字段。"""
    result = {}
    # { read write open ... }
    m = re.search(r'\{\s*([^}]+)\}', avc_line)
    if m:
        result['permissions'] = m.group(1).strip().split()
    # scontext=u:r:domain:s0
    m = re.search(r'scontext=\S*:(\w+):(\w+):', avc_line)
    if m:
        result['stype'] = m.group(2)  # source domain
    # tcontext=u:object_r:type:s0
    m = re.search(r'tcontext=\S*:(\w+):(\w+):', avc_line)
    if m:
        result['ttype'] = m.group(2)  # target type
    # tclass=file
    m = re.search(r'tclass=(\w+)', avc_line)
    if m:
        result['tclass'] = m.group(1)
    # comm="xxx"
    m = re.search(r'comm="([^"]+)"', avc_line)
    if m:
        result['comm'] = m.group(1)
    # name="xxx"
    m = re.search(r'name="([^"]+)"', avc_line)
    if m:
        result['name'] = m.group(1)
    # path="xxx"
    m = re.search(r'path="([^"]+)"', avc_line)
    if m:
        result['path'] = m.group(1)
    return result


def trace_avc(root: Path, avc_line: str):
    """从 avc denied 日志追踪相关策略。"""
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

    # 建议的 allow 规则
    if stype and ttype and tclass and perms:
        allow_rule = f'allow {stype} {ttype}:{tclass} {{ {" ".join(perms)} }};'
        emit('SUGGEST-ALLOW', '-', allow_rule)

    # 找 source domain 的 .te 文件
    if stype:
        trace_domain(root, stype)

    # 找 target type 的定义和引用
    if ttype:
        _find_type_def(root, ttype)

    # 找 path 对应的 file_contexts
    if info.get('path'):
        _find_file_context(root, info['path'])


def _find_file_context(root: Path, path: str):
    """在 file_contexts 里查找设备路径对应的 label。"""
    esc = re.escape(path)
    args = ['rg', '-n', '--no-heading',
            rf'{esc}',
            '-g', '*file_contexts*', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('FILE-CONTEXT', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def trace_domain(root: Path, domain: str):
    """按 SELinux domain 追踪。"""
    esc = re.escape(domain)

    # .te 文件（domain 名通常就是文件名）
    for line in run(['fd', '--type', 'f', rf'^{esc}\.te$', str(root)]).splitlines():
        if line.strip():
            emit('TE-FILE', line.strip(), f'policy file for {domain}')

    # type 声明：type domain, domain;
    args = ['rg', '-n', '--no-heading',
            rf'^\s*type\s+{esc}\s*,',
            '-g', '*.te', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('TYPE-DECL', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # allow/neverallow/auditallow/dontaudit 规则中引用该 domain
    args = ['rg', '-n', '--no-heading',
            rf'^\s*(allow|neverallow|auditallow|dontaudit)\s+{esc}\s',
            '-g', '*.te', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('ALLOW-RULE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 也找被 allow 的目标端引用
    args = ['rg', '-n', '--no-heading',
            rf'^\s*(allow|neverallow)\s+\w+\s+{esc}:',
            '-g', '*.te', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('ALLOW-RULE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # domain_auto_trans / domain_trans 宏
    args = ['rg', '-n', '--no-heading',
            rf'(domain_auto_trans|domain_trans|init_daemon_domain)\s*\(\s*{esc}\b',
            '-g', '*.te', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('DOMAIN-TRANS', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # .rc 文件里 seclabel 关联
    args = ['rg', '-n', '--no-heading',
            rf'seclabel\s+u:r:{esc}:',
            '-g', '*.rc', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('RC-SECLABEL', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def trace_device(root: Path, dev_path: str):
    """按设备节点追踪 file_contexts + 权限。"""
    esc = re.escape(dev_path)

    # file_contexts 里的 label
    args = ['rg', '-n', '--no-heading',
            rf'{esc}',
            '-g', '*file_contexts*', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('FILE-CONTEXT', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())
            # 提取 type
            cm = re.search(r':object_r:(\w+):', m.group(3))
            if cm:
                obj_type = cm.group(1)
                # 找哪些 domain 对这个 type 有 allow 规则
                _find_allow_for_type(root, obj_type)

    # genfs_contexts（proc/sysfs 节点）
    args = ['rg', '-n', '--no-heading',
            rf'{esc}',
            '-g', '*genfs_contexts*', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('GENFS-CONTEXT', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def trace_type(root: Path, se_type: str):
    """按 SELinux type 追踪。"""
    _find_type_def(root, se_type)
    _find_allow_for_type(root, se_type)

    # file_contexts 引用
    args = ['rg', '-n', '--no-heading',
            rf'{re.escape(se_type)}',
            '-g', '*file_contexts*', '-g', '*genfs_contexts*',
            '-g', '*service_contexts*', '-g', '*property_contexts*',
            str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('CONTEXT-REF', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def trace_service_context(root: Path, svc_name: str):
    """按 service 名追踪 service_contexts + 策略。"""
    esc = re.escape(svc_name)

    # service_contexts
    args = ['rg', '-n', '--no-heading',
            rf'^{esc}\s',
            '-g', '*service_contexts*', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('SVC-CONTEXT', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())
            # 提取 type
            cm = re.search(r':object_r:(\w+):', m.group(3))
            if cm:
                svc_type = cm.group(1)
                _find_type_def(root, svc_type)
                _find_allow_for_type(root, svc_type)

    # hwservice_contexts（HIDL）
    args = ['rg', '-n', '--no-heading',
            rf'{esc}',
            '-g', '*hwservice_contexts*', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('HWSVC-CONTEXT', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # vndservice_contexts（vendor service）
    args = ['rg', '-n', '--no-heading',
            rf'{esc}',
            '-g', '*vndservice_contexts*', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('VNDSVC-CONTEXT', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def _find_type_def(root: Path, se_type: str):
    """找 SELinux type 定义。"""
    esc = re.escape(se_type)
    args = ['rg', '-n', '--no-heading',
            rf'^\s*type\s+{esc}\s*[,;]',
            '-g', '*.te', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('TYPE-DECL', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def _find_allow_for_type(root: Path, se_type: str):
    """找所有 allow 规则中引用该 type 的（作为目标）。"""
    esc = re.escape(se_type)
    args = ['rg', '-n', '--no-heading',
            rf'^\s*(allow|neverallow)\s+\w+\s+{esc}[:\s]',
            '-g', '*.te', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('ALLOW-RULE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def do_scan(root: Path, out_path: Optional[Path]):
    """全量扫描所有 .te 文件和 type 声明。"""
    lines = []

    # 所有 .te 文件
    for fpath in run(['fd', '--type', 'f', r'\.te$', str(root)]).splitlines():
        if fpath.strip():
            lines.append(f'TE-FILE\t{fpath.strip()}')

    # 所有 type 声明
    args = ['rg', '-n', '--no-heading',
            r'^\s*type\s+(\w+)\s*,',
            '-g', '*.te', str(root)]
    for line in run(args, timeout=120).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            cm = re.search(r'type\s+(\w+)', m.group(3))
            if cm:
                lines.append(f'TYPE-DECL\t{m.group(1)}:{m.group(2)}\t{cm.group(1)}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='SELinux 策略 ↔ 代码/设备节点追踪')
    ap.add_argument('--avc', help='avc: denied 日志行（整行粘贴）')
    ap.add_argument('--domain', help='SELinux domain（如 hal_camera_default）')
    ap.add_argument('--device', help='设备节点路径（如 /dev/video0）')
    ap.add_argument('--type', dest='se_type', help='SELinux type（如 sysfs_camera）')
    ap.add_argument('--service-context', help='service 名（查 service_contexts）')
    ap.add_argument('--scan', action='store_true', help='全量扫描所有 .te 文件')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.avc:
        trace_avc(args.root, args.avc)
    elif args.domain:
        trace_domain(args.root, args.domain)
    elif args.device:
        trace_device(args.root, args.device)
    elif args.se_type:
        trace_type(args.root, args.se_type)
    elif args.service_context:
        trace_service_context(args.root, args.service_context)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
