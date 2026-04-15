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


def emit(tag: str, location: str, info: str = ''):
    print(f'{tag}\t{location}\t{info}')


# _IO 宏定义行，形如：
#   #define BINDER_WRITE_READ      _IOWR('b', 1, struct binder_write_read)
#   #define EVIOCGVERSION          _IOR('E', 0x01, int)
IOCTL_DEF_RE = re.compile(
    r'^#define\s+(\w+)\s+_IO(WR?|R|W)?\s*\(\s*'
    r"([^,]+?)\s*,\s*"          # type (char or expr)
    r"([^,\)]+?)"                 # nr
    r"(?:\s*,\s*([^)]+?))?"      # T (optional)
    r'\s*\)'
)


def parse_char_literal(s: str) -> Optional[int]:
    """解析 'X' 或十进制或十六进制。"""
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
    return ((d & 0x3) << 30) | ((size_val & 0x3fff) << 16) | ((type_val & 0xff) << 8) | (nr_val & 0xff)


def decode_cmd(cmd: int):
    d = (cmd >> 30) & 0x3
    size = (cmd >> 16) & 0x3fff
    t = (cmd >> 8) & 0xff
    nr = cmd & 0xff
    dir_name = {0: '_IOC_NONE', 1: '_IOC_WRITE', 2: '_IOC_READ', 3: '_IOC_RW'}[d]
    return {'dir': dir_name, 'type': t, 'type_char': chr(t) if 32 <= t < 127 else '?',
            'nr': nr, 'size': size}


def find_macro_defs(root: Path, macro_name: Optional[str] = None):
    """扫描 _IO* 宏定义。返回 [(file, line, name, dir_tag, type_str, nr_str, T_str, raw)]"""
    pat = r'#define\s+\w+\s+_IO(?:WR?|R|W)?\s*\('
    args = ['rg', '-n', '--no-heading', pat, '-g', '*.h', str(root)]
    results = []
    for line in run(args, timeout=120).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if not m:
            continue
        fpath, line_no, text = m.group(1), int(m.group(2)), m.group(3)
        mm = IOCTL_DEF_RE.match(text.strip())
        if not mm:
            continue
        name = mm.group(1)
        if macro_name and name != macro_name:
            continue
        dir_tag = mm.group(2) or ''
        results.append((fpath, line_no, name, dir_tag, mm.group(3), mm.group(4), mm.group(5) or '', text.strip()))
    return results


def find_handler_usages(root: Path, macro_name: str):
    """找 switch/case 里用到 macro 的位置——通常就是驱动 ioctl handler。"""
    args = ['rg', '-n', '--no-heading',
            rf'\bcase\s+{re.escape(macro_name)}\b',
            '-g', '*.c', '-g', '*.cpp', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('HANDLER-CASE', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 也报告 userspace 调用点 ioctl(fd, MACRO, ...)
    args = ['rg', '-n', '--no-heading',
            rf'ioctl\s*\([^,]+,\s*{re.escape(macro_name)}\b',
            '-g', '*.c', '-g', '*.cpp', '-g', '*.cc', str(root)]
    for line in run(args).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('USER-CALL', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def trace_handler(root: Path, handler: str):
    """
    给一个 ioctl handler 函数名（如 binder_ioctl），列出：
      1. 定义位置
      2. 该函数体内出现的所有 case <MACRO>: 行
      3. 被哪些 struct file_operations 引用（.unlocked_ioctl = xxx）
    """
    # 1. 定义
    for line in run(['rg', '-n', '--no-heading',
                     rf'^\s*(?:static\s+)?(?:long|int)\s+{re.escape(handler)}\s*\(',
                     '-g', '*.c', str(root)]).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('HANDLER-DEF', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())

    # 2. case 语句：简化做法，取 handler 所在文件里所有 case
    # 更精确需要解析 AST；这里用启发式：在每个命中文件里抓所有 case
    defs = run(['rg', '-n', '--files-with-matches',
                rf'^\s*(?:static\s+)?(?:long|int)\s+{re.escape(handler)}\s*\(',
                '-g', '*.c', str(root)]).splitlines()
    for fpath in defs:
        for line in run(['rg', '-n', r'\bcase\s+\w+:', fpath]).splitlines():
            m = re.match(r'^(\d+):(.*)$', line)
            if m:
                emit('HANDLER-CASE', f'{fpath}:{m.group(1)}', m.group(2).strip())

    # 3. file_operations 绑定
    for line in run(['rg', '-n', '--no-heading',
                     rf'\.unlocked_ioctl\s*=\s*{re.escape(handler)}\b',
                     '-g', '*.c', str(root)]).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('FOPS-BIND', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())
    for line in run(['rg', '-n', '--no-heading',
                     rf'\.compat_ioctl\s*=\s*{re.escape(handler)}\b',
                     '-g', '*.c', str(root)]).splitlines():
        m = re.match(r'^([^:]+):(\d+):(.*)$', line)
        if m:
            emit('FOPS-BIND-COMPAT', f'{m.group(1)}:{m.group(2)}', m.group(3).strip())


def trace_cmd(root: Path, cmd: int):
    """按命令号反查。先解码出 type/nr，再扫描所有宏定义找匹配。"""
    info = decode_cmd(cmd)
    print(f'# decoded: dir={info["dir"]}, type=0x{info["type"]:02x} '
          f'({info["type_char"]!r}), nr={info["nr"]}, size={info["size"]}',
          file=sys.stderr)

    # 扫所有宏定义
    defs = find_macro_defs(root)
    matched = []
    for fpath, line_no, name, dir_tag, type_str, nr_str, T_str, raw in defs:
        t = parse_char_literal(type_str)
        n = parse_char_literal(nr_str)
        if t is None or n is None:
            continue
        # size 无法不编译算出来（sizeof(T)），粗略比对 type+nr+dir
        dir_map = {'': 0, 'R': 2, 'W': 1, 'WR': 3}
        d = dir_map.get(dir_tag, 0)
        if t == info['type'] and n == info['nr'] and d == {
            '_IOC_NONE': 0, '_IOC_WRITE': 1, '_IOC_READ': 2, '_IOC_RW': 3
        }[info['dir']]:
            matched.append((fpath, line_no, name, raw))

    if not matched:
        print(f'# 未找到 type/nr/dir 匹配的宏；可能 type 非字符字面量，或宏跨多行',
              file=sys.stderr)
    for fpath, line_no, name, raw in matched:
        emit('MACRO-DEF', f'{fpath}:{line_no}', f'{name}  {raw}')
        # 顺带找 handler
        find_handler_usages(root, name)


def do_scan(root: Path, out_path: Optional[Path]):
    defs = find_macro_defs(root)
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
    ap = argparse.ArgumentParser(description='ioctl 跨边界追踪')
    ap.add_argument('--macro', help='宏名（如 BINDER_WRITE_READ）')
    ap.add_argument('--cmd', help='命令号（十进制或 0x... 十六进制）')
    ap.add_argument('--handler', help='驱动侧 ioctl handler 函数名（如 binder_ioctl）')
    ap.add_argument('--scan', action='store_true', help='扫描所有 _IO* 宏定义')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
        return

    if args.macro:
        defs = find_macro_defs(args.root, args.macro)
        for fpath, line_no, name, dir_tag, type_str, nr_str, T_str, raw in defs:
            t = parse_char_literal(type_str)
            n = parse_char_literal(nr_str)
            if t is not None and n is not None:
                # T 无法在此阶段算 size；用 0 先编出基础值作参考
                info = f'type={type_str} nr={nr_str} T={T_str}'
            else:
                info = raw
            emit('MACRO-DEF', f'{fpath}:{line_no}', f'_IO{dir_tag}  {info}')
        find_handler_usages(args.root, args.macro)
        return

    if args.cmd:
        cmd = int(args.cmd, 0)
        trace_cmd(args.root, cmd)
        return

    if args.handler:
        trace_handler(args.root, args.handler)
        return

    ap.print_help()
    sys.exit(1)


if __name__ == '__main__':
    main()
