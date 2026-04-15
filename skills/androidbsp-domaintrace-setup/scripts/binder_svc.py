#!/usr/bin/env python3
"""
Binder service 注册 ↔ 进程 ↔ VINTF manifest 追踪。

用法：
  # 按 service 名追踪
  binder_svc.py --service "camera.provider"
  binder_svc.py --service ICameraProvider

  # 按 .rc 进程名追踪（找该进程注册了哪些 service）
  binder_svc.py --process cameraserver

  # 按 VINTF HAL 名追踪
  binder_svc.py --hal android.hardware.camera.provider

  # 全量扫描 VINTF manifest
  binder_svc.py --scan [--out .binder_svc.idx]

识别链路：
  1. ServiceManager 注册
     C++:   ServiceManager::addService("name", ...)
            defaultServiceManager()->addService(...)
     Java:  ServiceManager.addService("name", ...)
  2. ServiceManager 获取
     C++:   ServiceManager::getService("name")
            IFoo::getService() (HIDL)
     Java:  ServiceManager.getService("name")
  3. .rc 文件：service xxx /vendor/bin/... 定义进程
  4. VINTF manifest：
     /vendor/manifest.xml
     /system/manifest.xml
     hardware/interfaces/**/manifest.xml
     device/**/manifest.xml

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


def trace_service(root: Path, svc_name: str):
    """按 service 名追踪：注册点、获取点、.rc 声明、VINTF。"""
    esc = re.escape(svc_name)

    # 1. C++ addService
    args = ['rg', '-n', '--no-heading',
            rf'addService\s*\([^)]*"{esc}"',
            '-g', '*.cpp', '-g', '*.cc', '-g', '*.h', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('SVC-REGISTER', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # Java addService
    args = ['rg', '-n', '--no-heading',
            rf'addService\s*\([^)]*"{esc}"',
            '-g', '*.java', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('SVC-REGISTER', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 2. C++ getService
    args = ['rg', '-n', '--no-heading',
            rf'getService\s*\([^)]*"{esc}"',
            '-g', '*.cpp', '-g', '*.cc', '-g', '*.h', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('SVC-GET', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # Java getService
    args = ['rg', '-n', '--no-heading',
            rf'getService\s*\([^)]*"{esc}"',
            '-g', '*.java', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('SVC-GET', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 如果 svc_name 以 I 开头，也找 HIDL getService 模式
    if svc_name.startswith('I'):
        args = ['rg', '-n', '--no-heading',
                rf'{esc}::getService\s*\(',
                '-g', '*.cpp', '-g', '*.cc', '-g', '*.h', str(root)]
        for line in run(args).splitlines():
            m = re.match(r'^([^:]+):(\d+):(.*)$', line)
            if m:
                emit('SVC-GET', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 3. .rc 文件：service 名与二进制路径
    args = ['rg', '-n', '--no-heading',
            rf'^\s*service\s+{esc}\s',
            '-g', '*.rc', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('RC-SERVICE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 也通过 interface 声明找 .rc 里的 interface aidl/hidl 行
    args = ['rg', '-n', '--no-heading',
            rf'^\s*interface\s+\w+\s+{esc}',
            '-g', '*.rc', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('RC-INTERFACE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 4. VINTF manifest
    _search_vintf(root, svc_name)

    # 5. service_contexts（SELinux 可能也关心）
    args = ['rg', '-n', '--no-heading',
            rf'^{esc}\s',
            '-g', '*service_contexts*', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('SVC-CONTEXT', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def trace_process(root: Path, proc_name: str):
    """按 .rc 进程名追踪：找该进程注册了哪些 service，以及 VINTF HAL。"""
    esc = re.escape(proc_name)

    # .rc 文件里 service 定义
    args = ['rg', '-n', '--no-heading',
            rf'^\s*service\s+{esc}\s',
            '-g', '*.rc', str(root)]
    rc_files = set()
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('RC-SERVICE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())
            rc_files.add(m.group(1))

    # 同 .rc 文件里的 interface 声明
    for rc in rc_files:
        for line in run(['rg', '-n', r'^\s*interface\s+', rc]).splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m:
                emit('RC-INTERFACE', f'{rc}:{m.group(1)}', m.group(2).strip())

    # 找进程二进制对应的源码目录（通过 Android.bp/Android.mk 里的 cc_binary 名）
    args = ['rg', '-n', '--no-heading',
            rf'name\s*:\s*"{esc}"',
            '-g', 'Android.bp', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('BUILD-DEF', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # VINTF：搜进程名在 manifest 里的 executable 属性
    args = ['rg', '-n', '--no-heading',
            rf'{esc}',
            '-g', 'manifest*.xml', '-g', 'compatibility_matrix*.xml', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('VINTF', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def trace_hal(root: Path, hal_name: str):
    """按 VINTF HAL FQDN 追踪（如 android.hardware.camera.provider）。"""
    esc = re.escape(hal_name)

    # VINTF manifest/compatibility_matrix
    args = ['rg', '-n', '--no-heading',
            rf'{esc}',
            '-g', 'manifest*.xml', '-g', 'compatibility_matrix*.xml',
            '-g', 'vintf/*.xml', '-g', 'manifest/*.xml',
            str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('VINTF', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # .rc 文件里 interface aidl/hidl 行
    args = ['rg', '-n', '--no-heading',
            rf'interface\s+\w+\s+{esc}',
            '-g', '*.rc', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('RC-INTERFACE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # AIDL 接口声明
    args = ['rg', '-n', '--no-heading',
            rf'package\s+{esc}',
            '-g', '*.aidl', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('AIDL-PACKAGE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # C++ 侧注册
    args = ['rg', '-n', '--no-heading',
            rf'"{esc}[/"]',
            '-g', '*.cpp', '-g', '*.cc', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('HAL-REF', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def _search_vintf(root: Path, keyword: str):
    """在 VINTF 相关 XML 文件中搜索关键字。"""
    args = ['rg', '-n', '--no-heading',
            re.escape(keyword),
            '-g', 'manifest*.xml', '-g', 'compatibility_matrix*.xml',
            '-g', 'vintf/*.xml',
            str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('VINTF', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def do_scan(root: Path, out_path: Optional[Path]):
    """全量扫描 VINTF manifest 里的 HAL 声明。"""
    lines = []

    # 扫 manifest.xml 里的 <hal> 块
    args = ['rg', '-n', '--no-heading',
            r'<name>([\w.]+)</name>',
            '-g', 'manifest*.xml', '-g', 'vintf/*.xml',
            str(root)]
    for line in run(args, timeout=120).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            cm = re.search(r'<name>([\w.]+)</name>', m.group(3))
            if cm:
                lines.append(f'VINTF-HAL\t{m.group(1)}:{m.group(2)}\t{cm.group(1)}')

    # 扫 .rc 文件里的 service + interface
    args = ['rg', '-n', '--no-heading',
            r'^\s*service\s+(\w+)\s',
            '-g', '*.rc', str(root)]
    for line in run(args, timeout=120).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            cm = re.search(r'service\s+(\w+)', m.group(3))
            if cm:
                lines.append(f'RC-SERVICE\t{m.group(1)}:{m.group(2)}\t{cm.group(1)}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='Binder service ↔ 进程 ↔ VINTF 追踪')
    ap.add_argument('--service', '-s', help='service 名（如 "camera.provider" 或 ICameraProvider）')
    ap.add_argument('--process', '-p', help='.rc 进程名（如 cameraserver）')
    ap.add_argument('--hal', help='VINTF HAL FQDN（如 android.hardware.camera.provider）')
    ap.add_argument('--scan', action='store_true', help='全量扫描 VINTF manifest')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.service:
        trace_service(args.root, args.service)
    elif args.process:
        trace_process(args.root, args.process)
    elif args.hal:
        trace_hal(args.root, args.hal)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
