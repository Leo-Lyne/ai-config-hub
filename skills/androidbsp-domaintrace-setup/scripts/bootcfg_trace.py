#!/usr/bin/env python3
"""bootcfg_trace.py — trace androidboot.* parameter sources.

In Android 11+, kernel cmdline + bootconfig (Android 12+) + DT chosen node
all contribute to androidboot.* params consumed by init/property service.
This script enumerates definitions and consumers for a given androidboot key.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import (
    Emitter, Finding, find_bsp_root, make_parser, rg_find, require_version,
    scan_partitions,
)

require_version("1.0.0")


def _bootconfig_files(bsp_root: Path) -> list[Path]:
    """Find vendor_boot bootconfig sources (Android 12+)."""
    found = []
    # Soong-generated bootconfig assembly inputs
    for cand in [bsp_root / 'device', bsp_root / 'vendor']:
        if cand.exists():
            for p in cand.rglob('bootconfig.txt'):
                found.append(p)
            for p in cand.rglob('vendor-bootconfig*'):
                found.append(p)
    return found


def _cmdline_sources(bsp_root: Path) -> list[Path]:
    """Find kernel cmdline sources (defconfig CMDLINE, BoardConfig, etc)."""
    found = []
    for cand in (bsp_root / 'device').rglob('BoardConfig*.mk') \
            if (bsp_root / 'device').exists() else []:
        found.append(cand)
    return found


def main():
    p = make_parser('Trace androidboot.<key> parameter sources & consumers.')
    p.add_argument('key', help='e.g. androidboot.serialno or just serialno')
    args = p.parse_args()

    key = args.key if args.key.startswith('androidboot.') \
                   else f'androidboot.{args.key}'

    try:
        bsp_root = Path(args.root) if args.root else find_bsp_root()
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(2)

    with Emitter(args, Path(__file__).name) as em:
        # 1) bootconfig 文件中的定义
        for bc_file in _bootconfig_files(bsp_root):
            for hit in rg_find(re.escape(key), globs=['*'], root=bc_file.parent):
                if str(bc_file).endswith(hit[0].split('/')[-1]):
                    em.emit(Finding(tag='BOOTCFG-DEF', file=hit[0],
                                    line=hit[1], snippet=hit[2],
                                    info={'source': 'bootconfig'}),
                            confidence='high', source='static-rg',
                            tags=['bootcfg'])

        # 2) BoardConfig.mk 中的 CMDLINE 定义
        for bc_file in _cmdline_sources(bsp_root):
            for hit in rg_find(rf'CMDLINE.*{re.escape(key)}',
                               root=bc_file.parent):
                em.emit(Finding(tag='CMDLINE-DEF', file=hit[0],
                                line=hit[1], snippet=hit[2],
                                info={'source': 'kernel-cmdline'}),
                        confidence='high', source='static-rg',
                        tags=['bootcfg'])

        # 3) DT chosen node
        for dts_dir in [bsp_root / 'kernel/common/arch',
                        bsp_root / 'kernel/private',
                        bsp_root / 'kernel']:
            if not dts_dir.exists():
                continue
            for hit in rg_find(rf'bootargs\s*=.*{re.escape(key)}',
                               globs=['*.dts*'], root=dts_dir):
                em.emit(Finding(tag='DT-CHOSEN', file=hit[0],
                                line=hit[1], snippet=hit[2],
                                info={'source': 'dt-chosen'}),
                        confidence='med', source='static-rg', tags=['bootcfg'])

        # 4) 消费方：init / property_service / system_properties
        for hit in rg_find(re.escape(key),
                           globs=['*.cpp', '*.c', '*.rc', '*.java', '*.kt'],
                           root=bsp_root):
            em.emit(Finding(tag='CONSUMER', file=hit[0], line=hit[1],
                            snippet=hit[2], info={}),
                    confidence='med', source='static-rg', tags=['bootcfg'])


if __name__ == '__main__':
    main()
