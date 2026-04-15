#!/usr/bin/env python3
"""
AIDL / HIDL 跨语言桥映射：接口声明 <-> 生成代码 <-> 实现类。

用法：
  # 按接口名追踪全链路
  aidl_bridge.py --interface IBluetoothHal
  aidl_bridge.py --interface IFoo --type hidl

  # 全量扫描所有 .aidl/.hal 声明
  aidl_bridge.py --scan [--out .aidl_bridge.idx]

依赖：
  - fd, rg, global（gtags 索引）
  - 编译产物（out/soong/.intermediates/）可选，用于定位生成代码

输出格式（TSV）：
  <tag>\t<file>[:<line>]\t<info>
  tag ∈ { DECL, GEN-JAVA, GEN-CPP, GEN-HEADER, IMPL, CLIENT }

AIDL 约定：
  - 声明：IFoo.aidl
  - 生成：out/soong/.intermediates/**/gen/**/{IFoo, BnFoo, BpFoo}.{h,cpp,java}
  - 实现：class/struct 继承 BnFoo（服务端）
  - 客户端：引用 BpFoo 或 IFoo.Stub.asInterface

HIDL 约定（android.hardware.*@X.Y::IFoo）：
  - 声明：hardware/interfaces/**/X.Y/IFoo.hal
  - 生成：out/soong/.intermediates/**/android.hardware.*@X.Y_genc++/gen/**/{IFoo, BnHwFoo, BpHwFoo, BsFoo}.h
  - 实现：继承 BnHwFoo 或 实现 IFoo
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ''


def fd_find(pattern: str, root: Path, extra_args=None):
    args = ['fd', '--type', 'f', pattern, str(root)]
    if extra_args:
        args.extend(extra_args)
    out = run(args)
    return [line for line in out.splitlines() if line.strip()]


def rg_find(pattern: str, globs=None, root: Optional[Path] = None):
    """返回 [(file, line, snippet)]"""
    args = ['rg', '-n', '--no-heading']
    if globs:
        for g in globs:
            args.extend(['-g', g])
    args.append(pattern)
    if root:
        args.append(str(root))
    out = run(args)
    results = []
    for ln in out.splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', ln)
        if m:
            results.append((m.group(1), int(m.group(2)), m.group(3).strip()))
    return results


def gtags_refs(sym: str):
    """global -rx <sym> -> [(file, line, snippet)]"""
    out = run(['global', '-rx', sym], timeout=30)
    results = []
    for ln in out.splitlines():
        parts = ln.split(None, 3)
        if len(parts) >= 4:
            results.append((parts[2], int(parts[1]), parts[3]))
    return results


def detect_type(iface_name: str, root: Path) -> str:
    """自动判断 interface 是 AIDL 还是 HIDL"""
    if fd_find(rf'^{re.escape(iface_name)}\.hal$', root, ['-t', 'f']):
        return 'hidl'
    if fd_find(rf'^{re.escape(iface_name)}\.aidl$', root, ['-t', 'f']):
        return 'aidl'
    return 'aidl'  # 默认


def emit(tag: str, location: str, info: str = ''):
    print(f'{tag}\t{location}\t{info}')


def trace_aidl(iface: str, root: Path):
    # 1. 声明 .aidl
    decls = fd_find(rf'^{re.escape(iface)}\.aidl$', root, ['-t', 'f'])
    for d in decls:
        emit('DECL', d, f'{iface}.aidl')

    # 2. 生成代码（编译产物，可能不存在）
    out_dir = root / 'out'
    if out_dir.exists():
        gen_java = fd_find(rf'^{re.escape(iface)}\.java$', out_dir)
        gen_cpp = fd_find(rf'^{re.escape(iface)}\.(cpp|cc|h)$', out_dir)
        for f in gen_java:
            if '.intermediates' in f:
                emit('GEN-JAVA', f, '')
        for f in gen_cpp:
            if '.intermediates' in f:
                tag = 'GEN-HEADER' if f.endswith('.h') else 'GEN-CPP'
                emit(tag, f, '')

    # 3. 实现类：继承 Bn<name-without-I> 或实现 IFoo.Stub
    base = iface[1:] if iface.startswith('I') else iface
    bn_name = f'Bn{base}'

    # C++ 侧实现
    for fpath, line, snip in rg_find(rf'class\s+\w+\s*:\s*public\s+{re.escape(bn_name)}\b',
                                     globs=['*.h', '*.cpp', '*.cc'], root=root):
        emit('IMPL', f'{fpath}:{line}', snip)

    # Java 侧实现：extends IFoo.Stub
    for fpath, line, snip in rg_find(rf'extends\s+{re.escape(iface)}\.Stub\b',
                                     globs=['*.java'], root=root):
        emit('IMPL', f'{fpath}:{line}', snip)

    # Kotlin 侧实现
    for fpath, line, snip in rg_find(rf':\s*{re.escape(iface)}\.Stub\b',
                                     globs=['*.kt'], root=root):
        emit('IMPL', f'{fpath}:{line}', snip)

    # 4. 客户端引用：Bp<base> 或 IFoo.Stub.asInterface
    bp_name = f'Bp{base}'
    for fpath, line, snip in rg_find(rf'\b{re.escape(bp_name)}\b',
                                     globs=['*.cpp', '*.cc', '*.h'], root=root):
        emit('CLIENT', f'{fpath}:{line}', snip)
    for fpath, line, snip in rg_find(rf'{re.escape(iface)}\.Stub\.asInterface\s*\(',
                                     globs=['*.java', '*.kt'], root=root):
        emit('CLIENT', f'{fpath}:{line}', snip)


def trace_hidl(iface: str, root: Path):
    # 1. 声明 .hal
    decls = fd_find(rf'^{re.escape(iface)}\.hal$', root, ['-t', 'f'])
    for d in decls:
        # 从路径提取 package 和 version，如 hardware/interfaces/foo/1.0/IFoo.hal
        m = re.search(r'(hardware/interfaces/[^/]+(?:/[^/]+)*?)/([\d.]+)/[^/]+$', d)
        pkg_info = ''
        if m:
            pkg_info = f'{m.group(1).replace("/", ".")}@{m.group(2)}'
        emit('DECL', d, pkg_info or f'{iface}.hal')

    # 2. 生成代码：HIDL 特有命名 BnHwFoo / BpHwFoo / BsFoo
    out_dir = root / 'out'
    if out_dir.exists():
        # 注意 HIDL 生成目录名含 @version
        gen_files = fd_find(rf'^{re.escape(iface)}\.h$', out_dir)
        for f in gen_files:
            if '.intermediates' in f:
                emit('GEN-HEADER', f, '')

    base = iface[1:] if iface.startswith('I') else iface
    bn_hw = f'BnHw{base}'
    bp_hw = f'BpHw{base}'
    bs_name = f'Bs{base}'

    # 3. C++ 实现：继承 BnHwFoo
    for fpath, line, snip in rg_find(rf'class\s+\w+\s*:\s*public\s+{re.escape(bn_hw)}\b',
                                     globs=['*.h', '*.cpp', '*.cc'], root=root):
        emit('IMPL', f'{fpath}:{line}', snip)

    # 也可能直接继承 IFoo（default impl 模式）
    for fpath, line, snip in rg_find(rf'class\s+\w+\s*:\s*public\s+{re.escape(iface)}\b',
                                     globs=['*.h', '*.cpp', '*.cc'], root=root):
        # 过滤掉位于 out/ 的生成文件（通常已由 BnHwFoo 覆盖）
        if '/out/' not in fpath:
            emit('IMPL', f'{fpath}:{line}', snip)

    # 4. 客户端
    for fpath, line, snip in rg_find(rf'\b({re.escape(bp_hw)}|{re.escape(bs_name)})\b',
                                     globs=['*.cpp', '*.cc', '*.h'], root=root):
        emit('CLIENT', f'{fpath}:{line}', snip)

    # 5. getService 调用（HIDL 客户端入口）
    for fpath, line, snip in rg_find(rf'{re.escape(iface)}::getService\s*\(',
                                     globs=['*.cpp', '*.cc', '*.h'], root=root):
        emit('CLIENT', f'{fpath}:{line}', snip)


def do_scan(root: Path, out_path: Optional[Path] = None):
    lines = []
    # 扫所有 .aidl / .hal
    aidl_files = fd_find(r'\.aidl$', root, ['--full-path', '-E', 'out'])
    hal_files = fd_find(r'\.hal$', root, ['--full-path', '-E', 'out'])
    for f in aidl_files:
        name = Path(f).stem
        if name.startswith('I'):
            lines.append(f'AIDL\t{f}\t{name}')
    for f in hal_files:
        name = Path(f).stem
        if name.startswith('I'):
            lines.append(f'HIDL\t{f}\t{name}')
    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='AIDL / HIDL 跨语言桥映射')
    ap.add_argument('--interface', '-i', metavar='IName',
                    help='接口名（必须以 I 开头，如 IBluetoothHal）')
    ap.add_argument('--type', choices=['aidl', 'hidl', 'auto'], default='auto',
                    help='接口类型；auto 自动探测（默认）')
    ap.add_argument('--scan', action='store_true',
                    help='扫描所有 .aidl/.hal 声明，产出索引')
    ap.add_argument('--out', type=Path, default=None,
                    help='--scan 的输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(),
                    help='BSP 根目录（默认当前目录）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
        return

    if not args.interface:
        ap.print_help()
        sys.exit(1)

    if not args.interface.startswith('I'):
        print(f'warning: AIDL/HIDL 接口约定以 I 开头（收到 {args.interface}）', file=sys.stderr)

    t = args.type
    if t == 'auto':
        t = detect_type(args.interface, args.root)

    if t == 'hidl':
        trace_hidl(args.interface, args.root)
    else:
        trace_aidl(args.interface, args.root)


if __name__ == '__main__':
    main()
