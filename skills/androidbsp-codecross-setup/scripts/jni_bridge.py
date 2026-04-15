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

输出格式（TSV）：
  <tag>\t<file>:<line>\t<snippet>
  tag ∈ { JAVA, KOTLIN, C }
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

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
    # 还原 _1 -> _
    # 做字符级还原，避免贪婪替换干扰
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
    # 最后一个未编码的 _ 之前是 FQCN（dots），之后是 method
    # 实际 mangling 把 . 也编码为 _，所以只能按"未编码 _"启发式切分：
    # 把末尾从右边第一个 _（不是来自 _1/_2/_3 之类的）切开即可。
    # 简化处理：默认最后一个 _ 即分隔符；返回多候选供调用方挨个试。
    candidates = []
    for idx in range(len(restored) - 1, -1, -1):
        if restored[idx] == '_':
            fqcn = restored[:idx].replace('_', '.')
            method = restored[idx+1:]
            if fqcn and method and re.match(r'^[A-Za-z_$][A-Za-z0-9_$]*$', method):
                candidates.append((fqcn, method))
    return candidates


# 扫描 Java 文件找 native 方法；返回 [(fqcn, method, file, line, snippet)]
def scan_java(bsp_root: Path):
    results = []
    # 用 rg 列出包含 'native' 的 Java 文件行，再自己定位类/包
    try:
        out = subprocess.run(
            ['rg', '-n', '--no-heading', '-t', 'java', r'\bnative\s+[\w\[\]<>,\s]+\s+\w+\s*\(', str(bsp_root)],
            capture_output=True, text=True, timeout=600,
        )
    except FileNotFoundError:
        print('rg not found', file=sys.stderr); return results
    for ln in out.stdout.splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', ln)
        if not m: continue
        fpath, line_no, snippet = m.group(1), int(m.group(2)), m.group(3)
        mm = re.search(r'\bnative\s+[\w\[\]<>,\s?]+?\s+(\w+)\s*\(', snippet)
        if not mm: continue
        method = mm.group(1)
        fqcn = resolve_java_fqcn(Path(fpath))
        if fqcn:
            results.append(('JAVA', fqcn, method, fpath, line_no, snippet.strip()))
    return results


def scan_kotlin(bsp_root: Path):
    results = []
    try:
        out = subprocess.run(
            ['rg', '-n', '--no-heading', '-g', '*.kt', r'\bexternal\s+fun\s+\w+\s*\(', str(bsp_root)],
            capture_output=True, text=True, timeout=600,
        )
    except FileNotFoundError:
        return results
    for ln in out.stdout.splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', ln)
        if not m: continue
        fpath, line_no, snippet = m.group(1), int(m.group(2)), m.group(3)
        mm = re.search(r'\bexternal\s+fun\s+(\w+)\s*\(', snippet)
        if not mm: continue
        method = mm.group(1)
        fqcn = resolve_kotlin_fqcn(Path(fpath))
        if fqcn:
            results.append(('KOTLIN', fqcn, method, fpath, line_no, snippet.strip()))
    return results


def resolve_java_fqcn(path: Path):
    try:
        text = path.read_text(errors='ignore')
    except Exception:
        return None
    pkg = re.search(r'^\s*package\s+([\w.]+)\s*;', text, re.M)
    cls = re.search(r'\b(?:class|interface|enum)\s+(\w+)', text)
    if not (pkg and cls): return None
    return f'{pkg.group(1)}.{cls.group(1)}'


def resolve_kotlin_fqcn(path: Path):
    try:
        text = path.read_text(errors='ignore')
    except Exception:
        return None
    pkg = re.search(r'^\s*package\s+([\w.]+)', text, re.M)
    cls = re.search(r'\b(?:class|object|interface)\s+(\w+)', text)
    if not (pkg and cls): return None
    return f'{pkg.group(1)}.{cls.group(1)}'


def gtags_lookup(sym: str):
    """用 global -d 找 C 符号定义；返回 [(file, line, snippet)]"""
    try:
        out = subprocess.run(
            ['global', '-x', '-d', sym],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return []
    results = []
    # global -x 输出格式: <symbol> <line> <file> <snippet>
    for ln in out.stdout.splitlines():
        parts = ln.split(None, 3)
        if len(parts) >= 4:
            _sym, line_no, fpath, snippet = parts
            results.append((fpath, int(line_no), snippet))
    return results


def from_java(fqcn: str, method: str):
    sym = mangle_jni(fqcn, method)
    print(f'# Mangled C symbol: {sym}')
    # Java 端
    # 通过 FQCN 反推文件路径提示；这里直接用 global 或 rg 找 native 声明
    try:
        cls_name = fqcn.rsplit('.', 1)[-1]
        res = subprocess.run(
            ['rg', '-n', '--no-heading',
             rf'\b(native|external)\s+.*\b{re.escape(method)}\s*\(',
             '-g', f'**/{cls_name}.java', '-g', f'**/{cls_name}.kt'],
            capture_output=True, text=True, timeout=30,
        )
        for ln in res.stdout.splitlines():
            m = re.match(r'^([^:]+):(\d+):(.*)$', ln)
            if m:
                tag = 'KOTLIN' if m.group(1).endswith('.kt') else 'JAVA'
                print(f'{tag}\t{m.group(1)}:{m.group(2)}\t{m.group(3).strip()}')
    except FileNotFoundError:
        print('rg not found', file=sys.stderr)
    # C 端（精确 + 带签名变体）
    for entry in gtags_lookup(sym):
        fpath, line_no, snippet = entry
        print(f'C\t{fpath}:{line_no}\t{snippet}')
    # 尝试匹配带签名的变体：Java_FQCN_method__...
    try:
        res = subprocess.run(
            ['global', '-x', '-c', sym],  # -c 前缀补全
            capture_output=True, text=True, timeout=30,
        )
        # -c 只返回符号列表，没有位置；对每个变体再跑 -d
        seen = {sym}
        for variant in res.stdout.splitlines():
            v = variant.strip()
            if v and v != sym and v not in seen:
                seen.add(v)
                for fpath, line_no, snippet in gtags_lookup(v):
                    print(f'C\t{fpath}:{line_no}\t{snippet}\t# variant {v}')
    except Exception:
        pass


def from_c(sym: str):
    cands = demangle_jni(sym) or []
    for fpath, line_no, snippet in gtags_lookup(sym):
        print(f'C\t{fpath}:{line_no}\t{snippet}')
    print(f'# Candidates (FQCN, method):')
    for fqcn, method in cands[:5]:
        print(f'#   {fqcn}\t{method}')
        # 尝试定位 Java/Kotlin 声明
        cls_name = fqcn.rsplit('.', 1)[-1] if '.' in fqcn else fqcn
        try:
            res = subprocess.run(
                ['rg', '-n', '--no-heading',
                 rf'\b(native|external)\s+.*\b{re.escape(method)}\s*\(',
                 '-g', f'**/{cls_name}.java', '-g', f'**/{cls_name}.kt'],
                capture_output=True, text=True, timeout=30,
            )
            for ln in res.stdout.splitlines():
                m = re.match(r'^([^:]+):(\d+):(.*)$', ln)
                if m:
                    tag = 'KOTLIN' if m.group(1).endswith('.kt') else 'JAVA'
                    print(f'{tag}\t{m.group(1)}:{m.group(2)}\t{m.group(3).strip()}')
        except FileNotFoundError:
            pass


def do_scan(bsp_root: Path, out_path: Optional[Path] = None):
    entries = scan_java(bsp_root) + scan_kotlin(bsp_root)
    lines = []
    for tag, fqcn, method, fpath, line_no, snippet in entries:
        mangled = mangle_jni(fqcn, method)
        # 反查 C
        c_hits = gtags_lookup(mangled)
        if c_hits:
            for cf, cl, cs in c_hits:
                lines.append(f'{tag}\t{fpath}:{line_no}\t{fqcn}.{method}\tC\t{cf}:{cl}\t{mangled}')
        else:
            lines.append(f'{tag}\t{fpath}:{line_no}\t{fqcn}.{method}\tC\t(not found)\t{mangled}')
    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='JNI 跨界桥映射')
    ap.add_argument('--from-java', nargs=2, metavar=('FQCN', 'METHOD'),
                    help='从 Java/Kotlin native 方法找 C 实现')
    ap.add_argument('--from-c', metavar='SYMBOL',
                    help='从 Java_*_*_* C 符号找 Java/Kotlin 声明')
    ap.add_argument('--scan', action='store_true',
                    help='全量扫描，产出索引')
    ap.add_argument('--out', type=Path, default=None,
                    help='--scan 的输出文件；不指定则写 stdout')
    ap.add_argument('--root', type=Path, default=Path.cwd(),
                    help='BSP 根目录（默认当前目录）')
    args = ap.parse_args()

    if args.from_java:
        from_java(*args.from_java)
    elif args.from_c:
        from_c(args.from_c)
    elif args.scan:
        do_scan(args.root, args.out)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
