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

注意：运行时 media-ctl 配置的 link 无法通过静态分析追踪。

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


def trace_subdev(root: Path, name: str):
    """追踪 v4l2 subdev driver。"""
    esc = re.escape(name)

    # v4l2_subdev_ops 定义
    for f, l, c in _rg(root, rf'v4l2_subdev_(pad_|video_|core_)?ops\b.*{esc}|{esc}.*v4l2_subdev_(pad_|video_|core_)?ops',
                        ['*.c']):
        emit('V4L2-OPS', f'{f}:{l}', c)

    # subdev init
    for f, l, c in _rg(root, rf'v4l2_(i2c_)?subdev_init\s*\(',
                        ['*.c']):
        if name in f.lower() or name.replace('-', '_') in f.lower():
            emit('V4L2-INIT', f'{f}:{l}', c)

    # media_entity_pads_init
    for f, l, c in _rg(root, rf'media_entity_pads_init\s*\(',
                        ['*.c']):
        if name in f.lower() or name.replace('-', '_') in f.lower():
            emit('MEDIA-PADS', f'{f}:{l}', c)

    # v4l2_async_register_subdev
    for f, l, c in _rg(root, rf'v4l2_async_register_subdev\w*\s*\(',
                        ['*.c']):
        if name in f.lower() or name.replace('-', '_') in f.lower():
            emit('V4L2-ASYNC-REG', f'{f}:{l}', c)

    # media_create_pad_link（在所有文件搜，因为 link 可能在 bridge driver 里建）
    for f, l, c in _rg(root, rf'media_create_pad_link\s*\([^)]*{esc}',
                        ['*.c']):
        emit('MEDIA-LINK', f'{f}:{l}', c)

    # 文件名匹配的源文件里找 probe
    for f, l, c in _rg(root, rf'\.probe\s*=',
                        ['*.c']):
        if name in f.lower() or name.replace('-', '_') in f.lower():
            emit('V4L2-PROBE', f'{f}:{l}', c)

    # DT compatible（sensor driver 通常在 of_device_id 里）
    for f, l, c in _rg(root, rf'\.compatible\s*=\s*"[^"]*{esc}[^"]*"',
                        ['*.c']):
        emit('V4L2-COMPAT', f'{f}:{l}', c)

    # DT 里的 port/endpoint
    _trace_dt_ports(root, name)


def _trace_dt_ports(root: Path, name: str):
    """在 DTS 里找包含 name 的节点下的 port/endpoint。"""
    esc = re.escape(name)

    # DTS 里包含该 compatible 的节点
    for f, l, c in _rg(root, rf'compatible\s*=\s*"[^"]*{esc}[^"]*"',
                        ['*.dts', '*.dtsi']):
        emit('V4L2-DT-NODE', f'{f}:{l}', c)

    # port@N / endpoint 节点（可能在 sensor 或 bridge 的 DTS 定义里）
    # 搜所有包含 port 的 DTS 文件
    for f, l, c in _rg(root, r'port(@\d+)?\s*\{',
                        ['*.dts', '*.dtsi']):
        if name in f.lower():
            emit('V4L2-DT-PORT', f'{f}:{l}', c)

    for f, l, c in _rg(root, r'endpoint(@\d+)?\s*\{',
                        ['*.dts', '*.dtsi']):
        if name in f.lower():
            emit('V4L2-DT-ENDPOINT', f'{f}:{l}', c)


def trace_entity(root: Path, name: str):
    """按 media entity 名搜索。"""
    esc = re.escape(name)

    for f, l, c in _rg(root, rf'"{esc}"',
                        ['*.c', '*.h']):
        if any(kw in c for kw in ['entity', 'media', 'subdev', 'sd->name', 'v4l2']):
            emit('ENTITY-REF', f'{f}:{l}', c)

    for f, l, c in _rg(root, rf'\.name\s*=\s*"{esc}"',
                        ['*.c']):
        emit('ENTITY-NAME', f'{f}:{l}', c)


def trace_port(root: Path, node: str):
    """追踪 DT port/endpoint 连接关系。"""
    esc = re.escape(node)

    # 找 compatible 节点
    for f, l, c in _rg(root, rf'compatible\s*=\s*"[^"]*{esc}[^"]*"',
                        ['*.dts', '*.dtsi']):
        emit('PORT-NODE', f'{f}:{l}', c)

    # remote-endpoint
    for f, l, c in _rg(root, rf'remote-endpoint\s*=\s*<&\w*{esc}\w*>',
                        ['*.dts', '*.dtsi']):
        emit('PORT-REMOTE', f'{f}:{l}', c)

    # v4l2_async_nf_add_fwnode / v4l2_async_notifier_add_*
    for f, l, c in _rg(root, r'v4l2_async_n[fo]_add_\w+\s*\(',
                        ['*.c']):
        emit('ASYNC-NOTIFIER', f'{f}:{l}', c)

    # of_graph_get_remote_endpoint / of_graph_get_next_endpoint
    for f, l, c in _rg(root, r'of_graph_get_(remote_endpoint|next_endpoint|port_by_id)\s*\(',
                        ['*.c']):
        emit('OF-GRAPH', f'{f}:{l}', c)


def do_scan(root: Path, out_path: Optional[Path]):
    lines = []

    # 所有 v4l2_subdev 注册
    for f, l, c in _rg(root, r'v4l2_(i2c_)?subdev_init\s*\(',
                        ['*.c'], timeout=300):
        lines.append(f'V4L2-INIT\t{f}:{l}\t{c}')

    # 所有 media_create_pad_link
    for f, l, c in _rg(root, r'media_create_pad_link\s*\(',
                        ['*.c'], timeout=300):
        lines.append(f'MEDIA-LINK\t{f}:{l}\t{c}')

    # 所有 v4l2_async_register_subdev
    for f, l, c in _rg(root, r'v4l2_async_register_subdev\w*\s*\(',
                        ['*.c'], timeout=300):
        lines.append(f'V4L2-ASYNC-REG\t{f}:{l}\t{c}')

    output = '\n'.join(lines)
    if out_path:
        out_path.write_text(output + '\n')
        print(f'Wrote {len(lines)} entries to {out_path}', file=sys.stderr)
    else:
        print(output)


def main():
    ap = argparse.ArgumentParser(description='V4L2 / Media Controller 静态拓扑追踪')
    ap.add_argument('--subdev', '-s', help='subdev driver 名（如 imx219）')
    ap.add_argument('--entity', '-e', help='media entity 名')
    ap.add_argument('--port', '-p', help='DT port/endpoint 追踪（compatible 或 node 名）')
    ap.add_argument('--scan', action='store_true', help='全量扫描所有 v4l2 subdev')
    ap.add_argument('--out', type=Path, default=None, help='--scan 输出文件')
    ap.add_argument('--root', type=Path, default=Path.cwd(), help='搜索根（默认 cwd）')
    args = ap.parse_args()

    if args.scan:
        do_scan(args.root, args.out)
    elif args.subdev:
        trace_subdev(args.root, args.subdev)
    elif args.entity:
        trace_entity(args.root, args.entity)
    elif args.port:
        trace_port(args.root, args.port)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
