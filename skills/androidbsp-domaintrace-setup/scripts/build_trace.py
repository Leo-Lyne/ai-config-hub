#!/usr/bin/env python3
"""
Android Build 系统追踪：模块定义 → 安装路径 + VNDK 可见性。

用法：
  # 追踪模块：Android.bp/mk → PRODUCT_PACKAGES → 安装分区
  build_trace.py --module camera.provider

  # 追踪 .so：反查哪个模块产出
  build_trace.py --so libcamera_provider.so

  # 查 VNDK 可见性
  build_trace.py --vndk libutils

  # 全量扫描
  build_trace.py --scan [--out .build.idx]

识别链路：
  1. Android.bp:  cc_binary / cc_library_shared / ... { name: "xxx" }
  2. Android.mk:  LOCAL_MODULE := xxx
  3. PRODUCT_PACKAGES: 拉进 image
  4. 安装路径:    从 module type 推断（cc_binary → /system/bin 等）
  5. VNDK:        vendor_available / vndk.enabled 属性

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


# 模块类型 → 典型安装路径
_INSTALL_HINTS = {
    'cc_binary': '/system/bin 或 /vendor/bin',
    'cc_library_shared': '/system/lib64 或 /vendor/lib64',
    'cc_library_static': '（不安装，静态链接）',
    'java_library': '/system/framework',
    'android_app': '/system/app 或 /system/priv-app',
    'cc_binary_host': '（host 工具，不装到设备）',
    'hal_service': '/vendor/bin/hw',
    'prebuilt_etc': '/system/etc 或 /vendor/etc',
}


def trace_module(root: Path, name: str):
    esc = re.escape(name)

    # 1. Android.bp: name: "xxx"
    for f, l, c in _rg(root, rf'name\s*:\s*"{esc}"',
                        ['Android.bp']):
        emit('BP-MODULE', f'{f}:{l}', c)
        # 回溯找 module type（在同文件 name 之前几行）
        _find_module_type(f, int(l))

    # 2. Android.mk: LOCAL_MODULE := xxx
    for f, l, c in _rg(root, rf'LOCAL_MODULE\s*:?=\s*{esc}\s*$',
                        ['Android.mk']):
        emit('MK-MODULE', f'{f}:{l}', c)

    # LOCAL_MODULE_PATH / LOCAL_MODULE_RELATIVE_PATH
    for f, l, c in _rg(root, rf'LOCAL_MODULE_(RELATIVE_)?PATH\s*:?=',
                        ['Android.mk']):
        if name in f or name.replace('.', '_') in f:
            emit('MK-INSTALL-PATH', f'{f}:{l}', c)

    # 3. PRODUCT_PACKAGES
    for f, l, c in _rg(root, rf'PRODUCT_PACKAGES\s*\+?=.*\b{esc}\b',
                        ['*.mk']):
        emit('PRODUCT-PKG', f'{f}:{l}', c)

    # 4. shared_libs / static_libs 引用（谁依赖这个模块）
    for f, l, c in _rg(root, rf'(shared_libs|static_libs|required)\s*:.*"{esc}"',
                        ['Android.bp']):
        emit('BP-DEP', f'{f}:{l}', c)

    # 5. VNDK 属性
    _check_vndk(root, name)


def _find_module_type(fpath: str, name_line: int):
    """从 Android.bp 里回溯找 module type。"""
    try:
        lines = Path(fpath).read_text().splitlines()
    except OSError:
        return
    # 往上找最近的 module type 声明（如 cc_binary {）
    for i in range(name_line - 1, max(name_line - 20, -1), -1):
        if i < 0 or i >= len(lines):
            continue
        m = re.match(r'^(\w+)\s*\{', lines[i])
        if m:
            mtype = m.group(1)
            hint = _INSTALL_HINTS.get(mtype, '')
            emit('BP-TYPE', f'{fpath}:{i+1}', f'{mtype}  →  {hint}' if hint else mtype)
            return


def _check_vndk(root: Path, name: str):
    """检查模块的 VNDK 属性。"""
    # 在包含该模块名的 Android.bp 里找 vendor_available / vndk
    for f, l, c in _rg(root, r'vendor_available\s*:\s*true',
                        ['Android.bp']):
        # 需要确认是同一个模块块内
        if _is_same_module_block(f, int(l), name):
            emit('VNDK-VENDOR', f'{f}:{l}', c)

    for f, l, c in _rg(root, r'vndk\s*:\s*\{',
                        ['Android.bp']):
        if _is_same_module_block(f, int(l), name):
            emit('VNDK-ENABLED', f'{f}:{l}', c)


def _is_same_module_block(fpath: str, attr_line: int, module_name: str) -> bool:
    """粗略判断属性行是否在包含 module_name 的模块块内。"""
    try:
        lines = Path(fpath).read_text().splitlines()
    except OSError:
        return False
    # 向前向后搜 name: "module_name"
    search_range = range(max(0, attr_line - 50), min(len(lines), attr_line + 50))
    for i in search_range:
        if f'"{module_name}"' in lines[i]:
            return True
    return False


def trace_so(root: Path, so_name: str):
    """反查 .so 文件对应的模块定义。"""
    # 去掉 lib 前缀和 .so 后缀得到可能的 module name
    bare = so_name
    if bare.startswith('lib'):
        bare = bare[3:]
    if bare.endswith('.so'):
        bare = bare[:-3]

    esc_so = re.escape(so_name)
    esc_bare = re.escape(bare)

    # Android.bp: name 匹配
    for f, l, c in _rg(root, rf'name\s*:\s*"(lib)?{esc_bare}"',
                        ['Android.bp']):
        emit('BP-MODULE', f'{f}:{l}', c)

    # stem 属性（输出文件名和模块名不同时）
    for f, l, c in _rg(root, rf'stem\s*:\s*"{esc_bare}"',
                        ['Android.bp']):
        emit('BP-STEM', f'{f}:{l}', c)

    # Android.mk
    for f, l, c in _rg(root, rf'LOCAL_MODULE\s*:?=\s*(lib)?{esc_bare}\s*$',
                        ['Android.mk']):
        emit('MK-MODULE', f'{f}:{l}', c)

    # 引用
    for f, l, c in _rg(root, rf'"{esc_so}"|"lib{esc_bare}"',
                        ['Android.bp', 'Android.mk']):
        emit('BUILD-REF', f'{f}:{l}', c)


def trace_vndk(root: Path, name: str):
    """检查模块的 VNDK 可见性。"""
    trace_module(root, name)  # 复用模块追踪，包含 VNDK 检查


def do_scan(root: Path, out_path: Optional[Path]):
    lines = []

    # 扫所有 Android.bp 模块
    for f, l, c in _rg(root, r'name\s*:\s*"([^"]+)"',
                        ['Android.bp'], timeout=300):
        cm = re.search(r'"([^"]+)"', c)
        if cm:
            lines.append(f'BP-MODULE\t{f}:{l}\t{cm.group(1)}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='Android Build 系统追踪')
    ap.add_argument('--module', '-m', help='模块名（如 camera.provider）')
    ap.add_argument('--so', help='.so 文件名（如 libcamera_provider.so）')
    ap.add_argument('--vndk', help='检查 VNDK 可见性')
    ap.add_argument('--scan', action='store_true', help='全量扫描所有模块')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.module:
        trace_module(args.root, args.module)
    elif args.so:
        trace_so(args.root, args.so)
    elif args.vndk:
        trace_vndk(args.root, args.vndk)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
