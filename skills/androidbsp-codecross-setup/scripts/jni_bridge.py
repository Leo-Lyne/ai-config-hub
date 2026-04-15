#!/usr/bin/env python3
"""
JNI 跨语言桥映射：Java/Kotlin native 方法 <-> C/C++ JNI 函数。

用法：
  # Java/Kotlin -> C
  jni_bridge.py --from-java <FQCN> <method>
  jni_bridge.py --from-java com.android.crypto.AesEngine encrypt

  # C -> Java/Kotlin
  jni_bridge.py --from-c <Java_*_*_*>
  jni_bridge.py --from-c Java_com_android_crypto_AesEngine_encrypt

  # 全量扫描，输出所有 native 方法的双端映射
  jni_bridge.py --scan [--out .jni_bridge.idx]

依赖：
  - gtags 索引（GTAGS/GRTAGS/GPATH），用于 C 侧反查
  - rg，用于 Java/Kotlin 侧扫描

Tags:
  NATIVE-DECL       Java/Kotlin native 方法声明
  JNI-IMPL          C/C++ 侧 Java_* 实现
  REGISTER-NATIVES  RegisterNatives / JNINativeMethod 动态注册
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Emitter, Finding, make_parser, require_version, run_cmd,
    rg_find, gtags_lookup,
)

require_version("1.0.0")


# JNI 名称 mangling 规则（JNI Spec 13.2）
# . -> _   (包分隔)
# _ -> _1
# ; -> _2
# [ -> _3
# $ -> _00024 (内部类)
def mangle_jni(fqcn: str, method: str) -> str:
    def enc(s: str) -> str:
        out = []
        for ch in s:
            if ch == '.' or ch == '/':
                out.append('_')
            elif ch == '_':
                out.append('_1')
            elif ch == ';':
                out.append('_2')
            elif ch == '[':
                out.append('_3')
            elif ch == '$':
                out.append('_00024')
            elif ord(ch) < 128 and (ch.isalnum() or ch == '_'):
                out.append(ch)
            else:
                out.append(f'_0{ord(ch):04x}')
        return ''.join(out)
    return f'Java_{enc(fqcn)}_{enc(method)}'


# 从 Java_... 反解析出 FQCN 和 method。不处理签名后缀（__Xxx）。
# 由于 mangling 不可逆（FQCN 与 method 的分隔只靠最后一个 _），
# 我们按最后一个 _ 切分；调用方若拿不到结果可再尝试倒数第二个。
def demangle_jni(sym: str):
    if not sym.startswith('Java_'):
        return None
    body = sym[len('Java_'):]
    # 移除签名后缀（__xxx）
    if '__' in body:
        body = body.split('__', 1)[0]
    # 还原 _1 -> _ 等
    out = []
    i = 0
    while i < len(body):
        if body[i] == '_' and i + 1 < len(body):
            nxt = body[i+1]
            if nxt == '1':
                out.append('_'); i += 2; continue
            elif nxt == '2':
                out.append(';'); i += 2; continue
            elif nxt == '3':
                out.append('['); i += 2; continue
            elif nxt == '0' and i + 5 < len(body):
                hex_ = body[i+2:i+6]
                try:
                    out.append(chr(int(hex_, 16))); i += 6; continue
                except ValueError:
                    pass
        out.append(body[i]); i += 1
    restored = ''.join(out)
    candidates = []
    for idx in range(len(restored) - 1, -1, -1):
        if restored[idx] == '_':
            fqcn = restored[:idx].replace('_', '.')
            method = restored[idx+1:]
            if fqcn and method and re.match(r'^[A-Za-z_$][A-Za-z0-9_$]*$', method):
                candidates.append((fqcn, method))
    return candidates


def resolve_java_fqcn(path: Path) -> Optional[str]:
    try:
        text = path.read_text(errors='ignore')
    except OSError:
        return None
    pkg = re.search(r'^\s*package\s+([\w.]+)\s*;', text, re.M)
    cls = re.search(r'\b(?:class|interface|enum)\s+(\w+)', text)
    if not (pkg and cls):
        return None
    return f'{pkg.group(1)}.{cls.group(1)}'


def resolve_kotlin_fqcn(path: Path) -> Optional[str]:
    try:
        text = path.read_text(errors='ignore')
    except OSError:
        return None
    pkg = re.search(r'^\s*package\s+([\w.]+)', text, re.M)
    cls = re.search(r'\b(?:class|object|interface)\s+(\w+)', text)
    if not (pkg and cls):
        return None
    return f'{pkg.group(1)}.{cls.group(1)}'


# 扫描 Java/Kotlin 文件找 native 方法
def scan_java(root: Path, timeout: int):
    results = []
    hits = rg_find(
        r'\bnative\s+[\w\[\]<>,\s]+\s+\w+\s*\(',
        globs=['*.java'], root=root, timeout=timeout,
    )
    for fpath, line_no, snippet in hits:
        mm = re.search(r'\bnative\s+[\w\[\]<>,\s?]+?\s+(\w+)\s*\(', snippet)
        if not mm:
            continue
        method = mm.group(1)
        fqcn = resolve_java_fqcn(Path(fpath))
        if fqcn:
            results.append(('java', fqcn, method, fpath, line_no, snippet))
    return results


def scan_kotlin(root: Path, timeout: int):
    results = []
    hits = rg_find(
        r'\bexternal\s+fun\s+\w+\s*\(',
        globs=['*.kt'], root=root, timeout=timeout,
    )
    for fpath, line_no, snippet in hits:
        mm = re.search(r'\bexternal\s+fun\s+(\w+)\s*\(', snippet)
        if not mm:
            continue
        method = mm.group(1)
        fqcn = resolve_kotlin_fqcn(Path(fpath))
        if fqcn:
            results.append(('kotlin', fqcn, method, fpath, line_no, snippet))
    return results


def find_register_natives(e: Emitter, sym: str, root: Optional[Path], timeout: int):
    """查 RegisterNatives / JNINativeMethod 数组中的动态注册。"""
    # 字符串字面量形式 "methodName"
    method_part = sym.rsplit('_', 1)[-1] if '_' in sym else sym
    if not method_part:
        return
    hits = rg_find(
        rf'"{re.escape(method_part)}"\s*,',
        globs=['*.cpp', '*.cc', '*.c', '*.h'], root=root, timeout=timeout,
    )
    for fpath, line_no, snippet in hits:
        # 启发式过滤：snippet 中同时出现 ( 及 函数指针符号，或在 JNINativeMethod 上下文内
        if 'JNINativeMethod' in snippet or re.search(r',\s*"[^"]*"\s*,\s*\w', snippet):
            e.emit(Finding(tag='REGISTER-NATIVES', file=fpath, line=line_no,
                           snippet=snippet,
                           info={'method': method_part}),
                   confidence='med', source='static-rg', tags=['jni', 'register'])


def from_java(e: Emitter, fqcn: str, method: str, root: Optional[Path],
              timeout: int):
    sym = mangle_jni(fqcn, method)
    print(f'# Mangled C symbol: {sym}', file=sys.stderr)

    # Java/Kotlin 声明
    cls_name = fqcn.rsplit('.', 1)[-1]
    hits = rg_find(
        rf'\b(native|external)\s+.*\b{re.escape(method)}\s*\(',
        globs=[f'**/{cls_name}.java', f'**/{cls_name}.kt'],
        root=root, timeout=timeout,
    )
    for fpath, line_no, snippet in hits:
        lang = 'kotlin' if fpath.endswith('.kt') else 'java'
        e.emit(Finding(tag='NATIVE-DECL', file=fpath, line=line_no,
                       snippet=snippet,
                       info={'lang': lang, 'fqcn': fqcn, 'method': method}),
               confidence='high', source='static-rg', tags=['jni', 'decl'])

    # C 侧精确 Java_*
    for fpath, line_no, snippet in gtags_lookup(sym, kind='def'):
        e.emit(Finding(tag='JNI-IMPL', file=fpath, line=line_no, snippet=snippet,
                       info={'symbol': sym}),
               confidence='high', source='gtags', tags=['jni', 'impl'])

    # 带签名后缀变体 Java_*__xxx
    r = run_cmd(['global', '-c', sym], timeout=timeout)
    if r.returncode in (0, 1):
        seen = {sym}
        for variant in r.stdout.splitlines():
            v = variant.strip()
            if v and v != sym and v not in seen:
                seen.add(v)
                for fpath, line_no, snippet in gtags_lookup(v, kind='def'):
                    e.emit(Finding(tag='JNI-IMPL', file=fpath, line=line_no,
                                   snippet=snippet,
                                   info={'symbol': v, 'variant': 'signed'}),
                           confidence='med', source='gtags',
                           tags=['jni', 'impl', 'variant'])

    # 动态注册
    find_register_natives(e, sym, root, timeout)


def from_c(e: Emitter, sym: str, root: Optional[Path], timeout: int):
    # C 端定义
    for fpath, line_no, snippet in gtags_lookup(sym, kind='def'):
        e.emit(Finding(tag='JNI-IMPL', file=fpath, line=line_no, snippet=snippet,
                       info={'symbol': sym}),
               confidence='high', source='gtags', tags=['jni', 'impl'])

    # 动态注册（去掉 Java_ 前缀也可能是纯名字）
    find_register_natives(e, sym, root, timeout)

    # demangle 候选 -> Java/Kotlin 声明
    cands = demangle_jni(sym) or []
    for fqcn, method in cands[:5]:
        cls_name = fqcn.rsplit('.', 1)[-1] if '.' in fqcn else fqcn
        hits = rg_find(
            rf'\b(native|external)\s+.*\b{re.escape(method)}\s*\(',
            globs=[f'**/{cls_name}.java', f'**/{cls_name}.kt'],
            root=root, timeout=timeout,
        )
        for fpath, line_no, snippet in hits:
            lang = 'kotlin' if fpath.endswith('.kt') else 'java'
            e.emit(Finding(tag='NATIVE-DECL', file=fpath, line=line_no,
                           snippet=snippet,
                           info={'lang': lang, 'fqcn': fqcn,
                                 'method': method, 'via': 'demangle'}),
                   confidence='med', source='static-rg',
                   tags=['jni', 'decl', 'demangle'])


def do_scan(e: Emitter, root: Path, out_path: Optional[Path], timeout: int):
    entries = scan_java(root, timeout) + scan_kotlin(root, timeout)
    lines = []
    for lang, fqcn, method, fpath, line_no, snippet in entries:
        mangled = mangle_jni(fqcn, method)
        # emit NATIVE-DECL
        e.emit(Finding(tag='NATIVE-DECL', file=fpath, line=line_no,
                       snippet=snippet,
                       info={'lang': lang, 'fqcn': fqcn, 'method': method,
                             'mangled': mangled}),
               confidence='high', source='static-rg', tags=['jni', 'decl'])
        c_hits = gtags_lookup(mangled, kind='def')
        if c_hits:
            for cf, cl, cs in c_hits:
                e.emit(Finding(tag='JNI-IMPL', file=cf, line=cl, snippet=cs,
                               info={'symbol': mangled, 'fqcn': fqcn,
                                     'method': method}),
                       confidence='high', source='gtags', tags=['jni', 'impl'])
                lines.append(f'{lang}\t{fpath}:{line_no}\t{fqcn}.{method}\tC\t{cf}:{cl}\t{mangled}')
        else:
            lines.append(f'{lang}\t{fpath}:{line_no}\t{fqcn}.{method}\tC\t(not found)\t{mangled}')
    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'# Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('JNI 跨界桥映射：Java/Kotlin native <-> C Java_*')
    p.add_argument('--from-java', nargs=2, metavar=('FQCN', 'METHOD'),
                   help='从 Java/Kotlin native 方法找 C 实现')
    p.add_argument('--from-c', metavar='SYMBOL',
                   help='从 Java_*_*_* C 符号找 Java/Kotlin 声明')
    p.add_argument('--scan', action='store_true',
                   help='全量扫描，产出索引')
    p.add_argument('--out', type=Path, default=None,
                   help='--scan 的索引文件（TSV），不指定则不额外写')
    args = p.parse_args()

    if not (args.from_java or args.from_c or args.scan):
        p.print_help()
        sys.exit(1)

    with Emitter(args, Path(__file__).name) as e:
        root = Path(args.root) if args.root else (e.bsp_root or Path.cwd())
        if args.from_java:
            from_java(e, args.from_java[0], args.from_java[1], root, args.timeout)
        elif args.from_c:
            from_c(e, args.from_c, root, args.timeout)
        elif args.scan:
            do_scan(e, root, args.out, args.timeout)


if __name__ == '__main__':
    main()
