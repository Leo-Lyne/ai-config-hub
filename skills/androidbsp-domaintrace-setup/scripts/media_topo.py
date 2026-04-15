#!/usr/bin/env python3
"""
V4L2 / Media Controller 静态拓扑追踪。

用法：
  # 按 subdev driver 名追踪
  media_topo.py --subdev imx219

  # 按 media entity 名追踪
  media_topo.py --entity "imx219 0-001a"

  # DT port/endpoint 追踪
  media_topo.py --port <compatible_or_node>

  # 全量扫描所有 v4l2_subdev 注册
  media_topo.py --scan [--out .media_topo.idx]

识别链路：
  1. v4l2_subdev_ops / v4l2_subdev_pad_ops      subdev 操作集
  2. v4l2_i2c_subdev_init / v4l2_subdev_init     subdev 初始化
  3. media_entity_pads_init                       pad 定义
  4. media_create_pad_link                        静态 link（build time）
  5. v4l2_async_register_subdev                   async 注册
  6. v4l2_async_nf_add_fwnode                     async notifier（DT 绑定）
  7. DT: port / endpoint                          CSI/DSI 连接描述

依赖：rg。
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Finding, Emitter, make_parser, rg_find, require_version,
)

require_version("1.0.0")


def trace_subdev(e: Emitter, root: Path, name: str):
    esc = re.escape(name)

    for f, l, snip in rg_find(
            rf'v4l2_subdev_(pad_|video_|core_)?ops\b.*{esc}|{esc}.*v4l2_subdev_(pad_|video_|core_)?ops',
            globs=['*.c'], root=root):
        e.emit(Finding(tag='V4L2-OPS', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['v4l2', 'subdev'])

    for f, l, snip in rg_find(r'v4l2_(i2c_)?subdev_init\s*\(',
                              globs=['*.c'], root=root):
        if name in f.lower() or name.replace('-', '_') in f.lower():
            e.emit(Finding(tag='V4L2-INIT', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg', tags=['v4l2'])

    for f, l, snip in rg_find(r'media_entity_pads_init\s*\(',
                              globs=['*.c'], root=root):
        if name in f.lower() or name.replace('-', '_') in f.lower():
            e.emit(Finding(tag='MEDIA-PADS', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg', tags=['media'])

    for f, l, snip in rg_find(r'v4l2_async_register_subdev\w*\s*\(',
                              globs=['*.c'], root=root):
        if name in f.lower() or name.replace('-', '_') in f.lower():
            e.emit(Finding(tag='V4L2-ASYNC-REG', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg', tags=['v4l2', 'async'])

    for f, l, snip in rg_find(rf'media_create_pad_link\s*\([^)]*{esc}',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='MEDIA-LINK', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['media', 'link'])

    for f, l, snip in rg_find(r'\.probe\s*=', globs=['*.c'], root=root):
        if name in f.lower() or name.replace('-', '_') in f.lower():
            e.emit(Finding(tag='V4L2-PROBE', file=f, line=l, snippet=snip),
                   confidence='med', source='static-rg', tags=['v4l2', 'probe'])

    for f, l, snip in rg_find(rf'\.compatible\s*=\s*"[^"]*{esc}[^"]*"',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='V4L2-COMPAT', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['v4l2', 'dt'])

    _trace_dt_ports(e, root, name)


def _trace_dt_ports(e: Emitter, root: Path, name: str):
    esc = re.escape(name)

    for f, l, snip in rg_find(rf'compatible\s*=\s*"[^"]*{esc}[^"]*"',
                              globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='V4L2-DT-NODE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['v4l2', 'dt'])

    for f, l, snip in rg_find(r'port(@\d+)?\s*\{',
                              globs=['*.dts', '*.dtsi'], root=root):
        if name in f.lower():
            e.emit(Finding(tag='V4L2-DT-PORT', file=f, line=l, snippet=snip),
                   confidence='low', source='static-rg', tags=['v4l2', 'dt'])

    for f, l, snip in rg_find(r'endpoint(@\d+)?\s*\{',
                              globs=['*.dts', '*.dtsi'], root=root):
        if name in f.lower():
            e.emit(Finding(tag='V4L2-DT-ENDPOINT', file=f, line=l, snippet=snip),
                   confidence='low', source='static-rg', tags=['v4l2', 'dt'])


def trace_entity(e: Emitter, root: Path, name: str):
    esc = re.escape(name)

    for f, l, snip in rg_find(rf'"{esc}"', globs=['*.c', '*.h'], root=root):
        if any(kw in snip for kw in ['entity', 'media', 'subdev', 'sd->name', 'v4l2']):
            e.emit(Finding(tag='ENTITY-REF', file=f, line=l, snippet=snip),
                   confidence='low', source='static-rg', tags=['media', 'entity'])

    for f, l, snip in rg_find(rf'\.name\s*=\s*"{esc}"',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='ENTITY-NAME', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['media', 'entity'])


def trace_port(e: Emitter, root: Path, node: str):
    esc = re.escape(node)

    for f, l, snip in rg_find(rf'compatible\s*=\s*"[^"]*{esc}[^"]*"',
                              globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='PORT-NODE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['dt', 'port'])

    for f, l, snip in rg_find(rf'remote-endpoint\s*=\s*<&\w*{esc}\w*>',
                              globs=['*.dts', '*.dtsi'], root=root):
        e.emit(Finding(tag='PORT-REMOTE', file=f, line=l, snippet=snip),
               confidence='high', source='static-rg', tags=['dt', 'port'])

    for f, l, snip in rg_find(r'v4l2_async_n[fo]_add_\w+\s*\(',
                              globs=['*.c'], root=root):
        e.emit(Finding(tag='ASYNC-NOTIFIER', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['v4l2', 'async'])

    for f, l, snip in rg_find(
            r'of_graph_get_(remote_endpoint|next_endpoint|port_by_id)\s*\(',
            globs=['*.c'], root=root):
        e.emit(Finding(tag='OF-GRAPH', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['dt', 'graph'])


def do_scan(e: Emitter, root: Path, out_path: Optional[Path]):
    lines = []

    for f, l, snip in rg_find(r'v4l2_(i2c_)?subdev_init\s*\(',
                              globs=['*.c'], root=root, timeout=300):
        lines.append(f'V4L2-INIT\t{f}:{l}\t{snip}')
        e.emit(Finding(tag='V4L2-INIT', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['v4l2', 'scan'])

    for f, l, snip in rg_find(r'media_create_pad_link\s*\(',
                              globs=['*.c'], root=root, timeout=300):
        lines.append(f'MEDIA-LINK\t{f}:{l}\t{snip}')
        e.emit(Finding(tag='MEDIA-LINK', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['media', 'scan'])

    for f, l, snip in rg_find(r'v4l2_async_register_subdev\w*\s*\(',
                              globs=['*.c'], root=root, timeout=300):
        lines.append(f'V4L2-ASYNC-REG\t{f}:{l}\t{snip}')
        e.emit(Finding(tag='V4L2-ASYNC-REG', file=f, line=l, snippet=snip),
               confidence='med', source='static-rg', tags=['v4l2', 'scan'])

    if out_path:
        out_path.write_text('\n'.join(lines) + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)


def main():
    p = make_parser('V4L2 / Media Controller 静态拓扑追踪')
    p.add_argument('--subdev', '-s', help='subdev driver 名（如 imx219）')
    p.add_argument('--entity', '-e', help='media entity 名')
    p.add_argument('--port', help='DT port/endpoint 追踪（compatible 或 node 名）')
    p.add_argument('--scan', action='store_true', help='全量扫描所有 v4l2 subdev')
    p.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    args = p.parse_args()

    search_root = args.root or Path.cwd()

    with Emitter(args, Path(__file__).name) as e:
        if args.scan:
            do_scan(e, search_root, args.out)
        elif args.subdev:
            trace_subdev(e, search_root, args.subdev)
        elif args.entity:
            trace_entity(e, search_root, args.entity)
        elif args.port:
            trace_port(e, search_root, args.port)
        else:
            p.print_help()
            sys.exit(1)


if __name__ == '__main__':
    main()
