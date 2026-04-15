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
  - rg, 可选 fd
  - 编译产物（out/soong/.intermediates/）可选，用于定位生成代码

Tags:
  AIDL-IFACE    .aidl 声明 / AIDL 生成代码（多 backend）
  HIDL-IFACE    .hal 声明 / HIDL 生成代码
  BN-IMPL       服务端实现（继承 Bn*/BnHw*/impl Rust trait）
  BP-CALLER     客户端引用（Bp*/BpHw*/asInterface/getService）

AIDL 约定：
  - 声明：IFoo.aidl
  - stable API：aidl_api/<pkg>/V<n>/<iface>.aidl（或 .../current/）
  - 多 backend 生成目录（Android 12+）：
      out/soong/.intermediates/**/<pkg>-V<n>-cpp-source/gen/...
      out/soong/.intermediates/**/<pkg>-V<n>-ndk-source/gen/...
      out/soong/.intermediates/**/<pkg>-V<n>-java-source/gen/...
      out/soong/.intermediates/**/<pkg>-V<n>-rust-source/gen/...
  - 实现：class/struct 继承 BnFoo（服务端）
  - 客户端：引用 BpFoo 或 IFoo.Stub.asInterface

HIDL 约定（android.hardware.*@X.Y::IFoo）：
  - 声明：hardware/interfaces/**/X.Y/IFoo.hal
  - 生成：out/soong/.intermediates/**/android.hardware.*@X.Y_genc++/gen/**/{IFoo, BnHwFoo, BpHwFoo, BsFoo}.h
  - 实现：继承 BnHwFoo 或 实现 IFoo
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Emitter, Finding, make_parser, require_version, run_cmd,
    rg_find,
)

require_version("1.0.0")


AIDL_BACKENDS = ('cpp', 'ndk', 'java', 'rust')
BACKEND_EXT_GLOBS = {
    'cpp':  ['*.h', '*.cpp'],
    'ndk':  ['*.h', '*.cpp'],
    'java': ['*.java'],
    'rust': ['*.rs'],
}


def fd_find(pattern: str, root: Path, extra_args=None, timeout: int = 60):
    args = ['fd', '--type', 'f', pattern, str(root)]
    if extra_args:
        args.extend(extra_args)
    r = run_cmd(args, timeout=timeout)
    if r.returncode not in (0, 1):
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


def detect_type(iface_name: str, root: Path, timeout: int) -> str:
    """自动判断 interface 是 AIDL 还是 HIDL。"""
    if fd_find(rf'^{re.escape(iface_name)}\.hal$', root, timeout=timeout):
        return 'hidl'
    if fd_find(rf'^{re.escape(iface_name)}\.aidl$', root, timeout=timeout):
        return 'aidl'
    return 'aidl'  # 默认


def enumerate_aidl_versions(iface: str, root: Path, timeout: int):
    """遍历 aidl_api/<pkg>/V<n>/ 下的 stable API 快照。
    返回 [(version, iface_path)]。"""
    results = []
    decls = fd_find(rf'^{re.escape(iface)}\.aidl$', root, timeout=timeout)
    for d in decls:
        m = re.search(r'/aidl_api/[^/]+/(V\d+|current)/', d)
        if m:
            results.append((m.group(1), d))
    return results


def find_backend_gen(iface: str, root: Path, timeout: int):
    """在 out/soong/.intermediates/ 下定位多 backend 生成代码。
    返回 [(backend, version, file_path)]。"""
    results = []
    out_dir = root / 'out'
    if not out_dir.exists():
        return results

    seen = set()
    for backend in AIDL_BACKENDS:
        # 文件名按 backend 约定：I<Name>.{h,cpp,java,rs}
        # 先收集可能的生成文件，再按目录名含 "-<backend>-source/" 过滤
        for ext in BACKEND_EXT_GLOBS[backend]:
            base = iface
            if ext == '*.rs':
                # AIDL rust backend 惯例：文件名多为 lib.rs / mangled；
                # 这里放宽到接口名片段匹配
                name_pat = rf'{re.escape(iface.lstrip("I"))}'
            else:
                name_pat = rf'^{re.escape(base)}\.'
            hits = fd_find(name_pat, out_dir,
                           extra_args=['-e', ext.lstrip('*.')],
                           timeout=timeout)
            for fpath in hits:
                if f'-{backend}-source' not in fpath:
                    continue
                m = re.search(rf'-(V\d+)-{backend}-source/', fpath)
                version = m.group(1) if m else ''
                key = (backend, version, fpath)
                if key not in seen:
                    seen.add(key)
                    results.append(key)
    return results


def trace_aidl(e: Emitter, iface: str, root: Path, timeout: int):
    # 1. 声明 .aidl（含 stable API 快照）
    decls = fd_find(rf'^{re.escape(iface)}\.aidl$', root, timeout=timeout)
    stable_versions = set()
    for d in decls:
        info = {'kind': 'decl'}
        m = re.search(r'/aidl_api/[^/]+/(V\d+|current)/', d)
        if m:
            info['stable'] = 'true'
            info['version'] = m.group(1)
            stable_versions.add(m.group(1))
        e.emit(Finding(tag='AIDL-IFACE', file=d, line=0,
                       snippet=f'{iface}.aidl', info=info),
               confidence='high', source='static-fd', tags=['aidl', 'decl'])

    if stable_versions:
        vstr = ', '.join(sorted(stable_versions))
        print(f'# AIDL stable versions for {iface}: {vstr}', file=sys.stderr)

    # 2. 多 backend 生成代码（cpp / ndk / java / rust）
    for backend, version, fpath in find_backend_gen(iface, root, timeout):
        e.emit(Finding(tag='AIDL-IFACE', file=fpath, line=0, snippet='',
                       info={'kind': 'gen', 'backend': backend,
                             'version': version}),
               confidence='high', source='static-fs',
               tags=['aidl', 'gen', backend])

    # 3. 实现类：继承 Bn<name-without-I> 或实现 IFoo.Stub
    base = iface[1:] if iface.startswith('I') else iface
    bn_name = f'Bn{base}'

    # C++ 侧实现（cpp/ndk backend 共用 Bn 基类名）
    for fpath, line, snip in rg_find(
            rf'class\s+\w+\s*:\s*public\s+{re.escape(bn_name)}\b',
            globs=['*.h', '*.cpp', '*.cc'], root=root, timeout=timeout):
        if '/out/' in fpath:
            continue
        e.emit(Finding(tag='BN-IMPL', file=fpath, line=line, snippet=snip,
                       info={'lang': 'cpp', 'base': bn_name}),
               confidence='high', source='static-rg',
               tags=['aidl', 'impl', 'cpp'])

    # Java 侧实现：extends IFoo.Stub
    for fpath, line, snip in rg_find(
            rf'extends\s+{re.escape(iface)}\.Stub\b',
            globs=['*.java'], root=root, timeout=timeout):
        if '/out/' in fpath:
            continue
        e.emit(Finding(tag='BN-IMPL', file=fpath, line=line, snippet=snip,
                       info={'lang': 'java', 'base': f'{iface}.Stub'}),
               confidence='high', source='static-rg',
               tags=['aidl', 'impl', 'java'])

    # Kotlin 侧实现
    for fpath, line, snip in rg_find(
            rf':\s*{re.escape(iface)}\.Stub\b',
            globs=['*.kt'], root=root, timeout=timeout):
        if '/out/' in fpath:
            continue
        e.emit(Finding(tag='BN-IMPL', file=fpath, line=line, snippet=snip,
                       info={'lang': 'kotlin', 'base': f'{iface}.Stub'}),
               confidence='high', source='static-rg',
               tags=['aidl', 'impl', 'kotlin'])

    # Rust 侧实现（AIDL rust backend）：impl I<Name>Async::... for / impl I<Name>:: for
    for fpath, line, snip in rg_find(
            rf'impl\s+{re.escape(iface)}\b',
            globs=['*.rs'], root=root, timeout=timeout):
        if '/out/' in fpath:
            continue
        e.emit(Finding(tag='BN-IMPL', file=fpath, line=line, snippet=snip,
                       info={'lang': 'rust', 'base': iface}),
               confidence='med', source='static-rg',
               tags=['aidl', 'impl', 'rust'])

    # 4. 客户端引用：Bp<base> 或 IFoo.Stub.asInterface
    bp_name = f'Bp{base}'
    for fpath, line, snip in rg_find(
            rf'\b{re.escape(bp_name)}\b',
            globs=['*.cpp', '*.cc', '*.h'], root=root, timeout=timeout):
        if '/out/' in fpath:
            continue
        e.emit(Finding(tag='BP-CALLER', file=fpath, line=line, snippet=snip,
                       info={'lang': 'cpp', 'proxy': bp_name}),
               confidence='med', source='static-rg',
               tags=['aidl', 'client', 'cpp'])

    for fpath, line, snip in rg_find(
            rf'{re.escape(iface)}\.Stub\.asInterface\s*\(',
            globs=['*.java', '*.kt'], root=root, timeout=timeout):
        lang = 'kotlin' if fpath.endswith('.kt') else 'java'
        e.emit(Finding(tag='BP-CALLER', file=fpath, line=line, snippet=snip,
                       info={'lang': lang, 'via': 'asInterface'}),
               confidence='high', source='static-rg',
               tags=['aidl', 'client', lang])


def trace_hidl(e: Emitter, iface: str, root: Path, timeout: int):
    # 1. 声明 .hal
    decls = fd_find(rf'^{re.escape(iface)}\.hal$', root, timeout=timeout)
    for d in decls:
        m = re.search(r'(hardware/interfaces/[^/]+(?:/[^/]+)*?)/([\d.]+)/[^/]+$', d)
        info = {'kind': 'decl'}
        if m:
            info['package'] = m.group(1).replace('/', '.')
            info['version'] = m.group(2)
        e.emit(Finding(tag='HIDL-IFACE', file=d, line=0,
                       snippet=f'{iface}.hal', info=info),
               confidence='high', source='static-fd', tags=['hidl', 'decl'])

    # 2. 生成代码：HIDL 特有命名 BnHwFoo / BpHwFoo / BsFoo
    out_dir = root / 'out'
    if out_dir.exists():
        gen_files = fd_find(rf'^{re.escape(iface)}\.h$', out_dir, timeout=timeout)
        for f in gen_files:
            if '.intermediates' in f:
                e.emit(Finding(tag='HIDL-IFACE', file=f, line=0, snippet='',
                               info={'kind': 'gen', 'lang': 'header'}),
                       confidence='high', source='static-fs',
                       tags=['hidl', 'gen'])

    base = iface[1:] if iface.startswith('I') else iface
    bn_hw = f'BnHw{base}'
    bp_hw = f'BpHw{base}'
    bs_name = f'Bs{base}'

    # 3. C++ 实现：继承 BnHwFoo
    for fpath, line, snip in rg_find(
            rf'class\s+\w+\s*:\s*public\s+{re.escape(bn_hw)}\b',
            globs=['*.h', '*.cpp', '*.cc'], root=root, timeout=timeout):
        e.emit(Finding(tag='BN-IMPL', file=fpath, line=line, snippet=snip,
                       info={'lang': 'cpp', 'base': bn_hw}),
               confidence='high', source='static-rg',
               tags=['hidl', 'impl', 'cpp'])

    # 也可能直接继承 IFoo（default impl 模式）
    for fpath, line, snip in rg_find(
            rf'class\s+\w+\s*:\s*public\s+{re.escape(iface)}\b',
            globs=['*.h', '*.cpp', '*.cc'], root=root, timeout=timeout):
        if '/out/' not in fpath:
            e.emit(Finding(tag='BN-IMPL', file=fpath, line=line, snippet=snip,
                           info={'lang': 'cpp', 'base': iface,
                                 'variant': 'default-impl'}),
                   confidence='med', source='static-rg',
                   tags=['hidl', 'impl', 'cpp'])

    # 4. 客户端
    for fpath, line, snip in rg_find(
            rf'\b({re.escape(bp_hw)}|{re.escape(bs_name)})\b',
            globs=['*.cpp', '*.cc', '*.h'], root=root, timeout=timeout):
        if '/out/' in fpath:
            continue
        e.emit(Finding(tag='BP-CALLER', file=fpath, line=line, snippet=snip,
                       info={'lang': 'cpp', 'proxy': bp_hw}),
               confidence='med', source='static-rg',
               tags=['hidl', 'client', 'cpp'])

    # 5. getService 调用（HIDL 客户端入口）
    for fpath, line, snip in rg_find(
            rf'{re.escape(iface)}::getService\s*\(',
            globs=['*.cpp', '*.cc', '*.h'], root=root, timeout=timeout):
        e.emit(Finding(tag='BP-CALLER', file=fpath, line=line, snippet=snip,
                       info={'lang': 'cpp', 'via': 'getService'}),
               confidence='high', source='static-rg',
               tags=['hidl', 'client', 'cpp'])


def do_scan(e: Emitter, root: Path, out_path: Optional[Path], timeout: int):
    lines = []
    aidl_files = fd_find(r'\.aidl$', root, ['--full-path', '-E', 'out'],
                         timeout=timeout)
    hal_files = fd_find(r'\.hal$', root, ['--full-path', '-E', 'out'],
                        timeout=timeout)
    for f in aidl_files:
        name = Path(f).stem
        if name.startswith('I'):
            e.emit(Finding(tag='AIDL-IFACE', file=f, line=0, snippet=name,
                           info={'kind': 'decl'}),
                   confidence='high', source='static-fd', tags=['aidl', 'scan'])
            lines.append(f'AIDL\t{f}\t{name}')
    for f in hal_files:
        name = Path(f).stem
        if name.startswith('I'):
            e.emit(Finding(tag='HIDL-IFACE', file=f, line=0, snippet=name,
                           info={'kind': 'decl'}),
                   confidence='high', source='static-fd', tags=['hidl', 'scan'])
            lines.append(f'HIDL\t{f}\t{name}')
    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'# Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('AIDL / HIDL 跨语言桥映射')
    p.add_argument('--interface', '-i', metavar='IName',
                   help='接口名（必须以 I 开头，如 IBluetoothHal）')
    p.add_argument('--type', choices=['aidl', 'hidl', 'auto'], default='auto',
                   help='接口类型；auto 自动探测（默认）')
    p.add_argument('--scan', action='store_true',
                   help='扫描所有 .aidl/.hal 声明，产出索引')
    p.add_argument('--out', type=Path, default=None,
                   help='--scan 的 TSV 输出文件')
    args = p.parse_args()

    if not (args.interface or args.scan):
        p.print_help()
        sys.exit(1)

    with Emitter(args, Path(__file__).name) as e:
        root = Path(args.root) if args.root else (e.bsp_root or Path.cwd())

        if args.scan:
            do_scan(e, root, args.out, args.timeout)
            return

        if not args.interface.startswith('I'):
            print(f'warning: AIDL/HIDL 接口约定以 I 开头（收到 {args.interface}）',
                  file=sys.stderr)

        t = args.type
        if t == 'auto':
            t = detect_type(args.interface, root, args.timeout)

        if t == 'hidl':
            trace_hidl(e, args.interface, root, args.timeout)
        else:
            trace_aidl(e, args.interface, root, args.timeout)


if __name__ == '__main__':
    main()
