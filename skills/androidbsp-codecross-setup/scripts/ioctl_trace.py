#!/usr/bin/env python3
"""
ioctl 跨边界追踪：userspace 命令号 <-> _IO* 宏定义 <-> 驱动 handler switch case。

用法：
  # 按宏名追：找宏定义 + 找 handler 使用点
  ioctl_trace.py --macro BINDER_WRITE_READ

  # 按命令号追：展开宏反查（十进制或 0x...）
  ioctl_trace.py --cmd 0xc0186201

  # 按驱动 handler 函数追：定位 switch case 列表
  ioctl_trace.py --handler binder_ioctl

  # 全量扫描：列出所有 _IO* 宏定义
  ioctl_trace.py --scan [--out .ioctl.idx]

ioctl 命令号编码（Linux asm-generic/ioctl.h）：
  cmd = (dir << 30) | (size << 16) | (type << 8) | nr
  dir:  _IOC_NONE=0, _IOC_WRITE=1, _IOC_READ=2, _IOC_RW=3
  宏：
    _IO(type, nr)                dir=0
    _IOR(type, nr, T)            dir=2 (READ，驱动->用户)
    _IOW(type, nr, T)            dir=1 (WRITE，用户->驱动)
    _IOWR(type, nr, T)           dir=3

依赖：rg, fd, 可选 gtags（global）。
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Emitter, Finding, make_parser, require_version, run_cmd,
)

require_version("1.0.0")


IOCTL_DEF_RE = re.compile(
    r'^#define\s+(\w+)\s+_IO(WR?|R|W)?\s*\(\s*'
    r"([^,]+?)\s*,\s*"
    r"([^,\)]+?)"
    r"(?:\s*,\s*([^)]+?))?"
    r'\s*\)'
)


def parse_char_literal(s: str) -> Optional[int]:
    s = s.strip()
    m = re.match(r"^'(.)'$", s)
    if m:
        return ord(m.group(1))
    try:
        return int(s, 0)
    except ValueError:
        return None


def compute_cmd(dir_tag: str, type_val: int, nr_val: int, size_val: int) -> int:
    dir_map = {'': 0, 'R': 2, 'W': 1, 'WR': 3}
    d = dir_map.get(dir_tag, 0)
    return ((d & 0x3) << 30) | ((size_val & 0x3fff) << 16) | \
           ((type_val & 0xff) << 8) | (nr_val & 0xff)


def decode_cmd(cmd: int):
    d = (cmd >> 30) & 0x3
    size = (cmd >> 16) & 0x3fff
    t = (cmd >> 8) & 0xff
    nr = cmd & 0xff
    dir_name = {0: '_IOC_NONE', 1: '_IOC_WRITE', 2: '_IOC_READ', 3: '_IOC_RW'}[d]
    return {'dir': dir_name, 'type': t,
            'type_char': chr(t) if 32 <= t < 127 else '?',
            'nr': nr, 'size': size}


def _rg_lines(pattern: str, globs: list[str], root: Path, timeout: int):
    cmd = ['rg', '-n', '--no-heading']
    for g in globs:
        cmd += ['-g', g]
    cmd += [pattern, str(root)]
    r = run_cmd(cmd, timeout=timeout)
    out = []
    for line in r.stdout.splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            out.append((m.group(1), int(m.group(2)), m.group(3)))
    return out


def find_macro_defs(root: Path, timeout: int,
                    macro_name: Optional[str] = None):
    """扫描 _IO* 宏定义。返回 [(file, line, name, dir_tag, type_str, nr_str, T_str, raw)]"""
    pat = r'#define\s+\w+\s+_IO(?:WR?|R|W)?\s*\('
    results = []
    for fpath, line_no, text in _rg_lines(pat, ['*.h'], root, timeout):
        mm = IOCTL_DEF_RE.match(text.strip())
        if not mm:
            continue
        name = mm.group(1)
        if macro_name and name != macro_name:
            continue
        dir_tag = mm.group(2) or ''
        results.append((fpath, line_no, name, dir_tag,
                        mm.group(3), mm.group(4),
                        mm.group(5) or '', text.strip()))
    return results


def find_handler_usages(e: Emitter, root: Path, macro_name: str, timeout: int):
    """找 switch/case 里用到 macro 的位置 + userspace 调用点。"""
    for fpath, line_no, snip in _rg_lines(
            rf'\bcase\s+{re.escape(macro_name)}\b',
            ['*.c', '*.cpp'], root, timeout):
        e.emit(Finding(tag='HANDLER-CASE', file=fpath, line=line_no,
                       snippet=snip.strip()),
               confidence='high', source='static-rg',
               tags=['ioctl', 'handler'])

    for fpath, line_no, snip in _rg_lines(
            rf'ioctl\s*\([^,]+,\s*{re.escape(macro_name)}\b',
            ['*.c', '*.cpp', '*.cc'], root, timeout):
        e.emit(Finding(tag='USER-CALL', file=fpath, line=line_no,
                       snippet=snip.strip()),
               confidence='med', source='static-rg',
               tags=['ioctl', 'user'])


def trace_handler(e: Emitter, root: Path, handler: str, timeout: int):
    """给 ioctl handler 函数名，列出定义/所有 case/fops 绑定。"""
    for fpath, line_no, snip in _rg_lines(
            rf'^\s*(?:static\s+)?(?:long|int)\s+{re.escape(handler)}\s*\(',
            ['*.c'], root, timeout):
        e.emit(Finding(tag='HANDLER-DEF', file=fpath, line=line_no,
                       snippet=snip.strip()),
               confidence='high', source='static-rg',
               tags=['ioctl', 'handler'])

    r = run_cmd(['rg', '-n', '--files-with-matches',
                 rf'^\s*(?:static\s+)?(?:long|int)\s+{re.escape(handler)}\s*\(',
                 '-g', '*.c', str(root)], timeout=timeout)
    for fpath in r.stdout.splitlines():
        if not fpath.strip():
            continue
        rr = run_cmd(['rg', '-n', r'\bcase\s+\w+:', fpath], timeout=timeout)
        for line in rr.stdout.splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m:
                e.emit(Finding(tag='HANDLER-CASE', file=fpath,
                               line=int(m.group(1)),
                               snippet=m.group(2).strip()),
                       confidence='med', source='static-rg',
                       tags=['ioctl', 'handler'])

    for fpath, line_no, snip in _rg_lines(
            rf'\.unlocked_ioctl\s*=\s*{re.escape(handler)}\b',
            ['*.c'], root, timeout):
        e.emit(Finding(tag='FOPS-BIND', file=fpath, line=line_no,
                       snippet=snip.strip()),
               confidence='high', source='static-rg',
               tags=['ioctl', 'fops'])
    for fpath, line_no, snip in _rg_lines(
            rf'\.compat_ioctl\s*=\s*{re.escape(handler)}\b',
            ['*.c'], root, timeout):
        e.emit(Finding(tag='FOPS-BIND-COMPAT', file=fpath, line=line_no,
                       snippet=snip.strip()),
               confidence='high', source='static-rg',
               tags=['ioctl', 'fops', 'compat'])


def trace_cmd(e: Emitter, root: Path, cmd: int, timeout: int):
    """按命令号反查。先解码出 type/nr，再扫描所有宏定义找匹配。"""
    info = decode_cmd(cmd)
    print(f'# decoded: dir={info["dir"]}, type=0x{info["type"]:02x} '
          f'({info["type_char"]!r}), nr={info["nr"]}, size={info["size"]}',
          file=sys.stderr)

    defs = find_macro_defs(root, timeout)
    matched = []
    dir_map = {'': 0, 'R': 2, 'W': 1, 'WR': 3}
    dir_int = {'_IOC_NONE': 0, '_IOC_WRITE': 1,
               '_IOC_READ': 2, '_IOC_RW': 3}[info['dir']]
    for fpath, line_no, name, dir_tag, type_str, nr_str, T_str, raw in defs:
        t = parse_char_literal(type_str)
        n = parse_char_literal(nr_str)
        if t is None or n is None:
            continue
        d = dir_map.get(dir_tag, 0)
        if t == info['type'] and n == info['nr'] and d == dir_int:
            matched.append((fpath, line_no, name, raw))

    if not matched:
        print(f'# 未找到 type/nr/dir 匹配的宏；可能 type 非字符字面量，或宏跨多行',
              file=sys.stderr)
    for fpath, line_no, name, raw in matched:
        e.emit(Finding(tag='MACRO-DEF', file=fpath, line=line_no,
                       snippet=f'{name}  {raw}'),
               confidence='high', source='static-rg',
               tags=['ioctl', 'macro'])
        find_handler_usages(e, root, name, timeout)


def do_scan(root: Path, timeout: int, out_path: Optional[Path]):
    defs = find_macro_defs(root, timeout)
    lines = []
    for fpath, line_no, name, dir_tag, type_str, nr_str, T_str, raw in defs:
        lines.append(f'{name}\t{fpath}:{line_no}\t_IO{dir_tag}\t'
                     f'type={type_str}\tnr={nr_str}\tT={T_str}')
    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} macro defs to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    p = make_parser('ioctl trace: macro ↔ cmd number ↔ driver handler')
    p.add_argument('--macro', help='宏名（如 BINDER_WRITE_READ）')
    p.add_argument('--cmd', help='命令号（十进制或 0x... 十六进制）')
    p.add_argument('--handler', help='驱动侧 ioctl handler 函数名（如 binder_ioctl）')
    p.add_argument('--scan', action='store_true', help='扫描所有 _IO* 宏定义')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    with Emitter(args, Path(__file__).name) as e:
        root = Path(args.root) if args.root else (e.bsp_root or Path.cwd())

        if args.scan:
            do_scan(root, args.timeout, args.out)
            return

        if args.macro:
            defs = find_macro_defs(root, args.timeout, args.macro)
            for fpath, line_no, name, dir_tag, type_str, nr_str, T_str, raw in defs:
                t = parse_char_literal(type_str)
                n = parse_char_literal(nr_str)
                if t is not None and n is not None:
                    info = f'type={type_str} nr={nr_str} T={T_str}'
                else:
                    info = raw
                e.emit(Finding(tag='MACRO-DEF', file=fpath, line=line_no,
                               snippet=f'_IO{dir_tag}  {info}'),
                       confidence='high', source='static-rg',
                       tags=['ioctl', 'macro'])
            find_handler_usages(e, root, args.macro, args.timeout)
            return

        if args.cmd:
            cmd = int(args.cmd, 0)
            trace_cmd(e, root, cmd, args.timeout)
            return

        if args.handler:
            trace_handler(e, root, args.handler, args.timeout)
            return

        p.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
