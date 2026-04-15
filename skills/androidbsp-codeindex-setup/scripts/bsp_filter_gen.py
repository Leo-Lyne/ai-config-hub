#!/usr/bin/env python3
"""
bsp_filter_gen.py — Android BSP Active File Index Generator

Platform-agnostic: works with Rockchip, Qualcomm, MediaTek, Unisoc, and
any AOSP-derived BSP that follows the standard device/<vendor>/ layout.

Generates .active_files.idx by combining three data sources:
  1. C/C++ source files  — from compile_commands.json
  2. Java/Kotlin modules  — from module-info.json + installed-files.txt
  3. Target-specific configs — DTS include chain, defconfig, device configs
     (requires --build-cmd)

Usage:
  # With build command (recommended — enables DTS/defconfig tracing)
  python3 scripts/bsp_filter_gen.py \\
    -b "source build/envsetup.sh && lunch <product>-<variant>"

  # Without build command (compdb + module-info only)
  python3 scripts/bsp_filter_gen.py
"""
import os
import sys
import json
import re
import argparse
import glob as globmod
from pathlib import Path
from typing import Optional

# ── constants ───────────────────────────────────────────────────────────

SKIP_EXTS = frozenset((
    '.o', '.a', '.so', '.unstripped', '.obj', '.d', '.P', '.cmd',
    '.order', '.builtin', '.tmp', '.swp', '.pyc', '.class',
))

JAVA_MODULE_EXTS = frozenset((
    '.java', '.kt', '.aidl', '.hal', '.xml', '.mk', '.bp',
))

CONFIG_EXTS = frozenset((
    '.mk', '.cfg', '.sh', '.xml', '.prop', '.in', '.rc',
))

# Possible kernel root directories (checked in order)
KERNEL_DIR_CANDIDATES = [
    'kernel',
    'kernel-5.15', 'kernel-5.10', 'kernel-5.4', 'kernel-4.19', 'kernel-4.14', 'kernel-4.9',
    'bsp/kernel',
    'vendor/kernel',
]

# Known vendor → DTS subdirectory mappings (fallback if auto-detect fails)
VENDOR_DTS_MAP = {
    'rockchip': 'rockchip',
    'qcom':     'qcom',
    'mediatek': 'mediatek',
    'sprd':     'sprd',
    'unisoc':   'unisoc',
    'samsung':  'exynos',
    'amlogic':  'amlogic',
    'allwinner': 'allwinner',
}

# BoardConfig variable names across vendors (all checked, first match wins)
BOARDCONFIG_VARS = [
    'PRODUCT_KERNEL_DTS',    # Rockchip
    'BOARD_KERNEL_DTS',      # some Rockchip variants
    'TARGET_KERNEL_DT',      # Qualcomm
    'KERNEL_DTS',            # generic
    'PRODUCT_KERNEL_CONFIG', # Rockchip
    'KERNEL_DEFCONFIG',      # Qualcomm / MTK / generic
    'TARGET_KERNEL_CONFIG',  # Qualcomm
    'PRODUCT_UBOOT_CONFIG',  # Rockchip
    'PRODUCT_KERNEL_ARCH',   # Rockchip
    'TARGET_KERNEL_ARCH',    # Qualcomm
    'PRODUCT_DTBO_TEMPLATE', # Rockchip
    'MTK_PLATFORM',          # MTK
    'MTK_TARGET_PROJECT',    # MTK
    'TARGET_BOARD_PLATFORM', # generic AOSP
]


# ── 1. C/C++ from compile_commands.json ─────────────────────────────────

def get_cpp_files(compdb_path):
    files = set()
    if not os.path.exists(compdb_path):
        print(f"[SKIP] {compdb_path} not found", file=sys.stderr)
        return files
    print(f"Parsing {compdb_path} ...", file=sys.stderr)
    pattern = re.compile(r'"file":\s*"([^"]+)"')
    with open(compdb_path, 'r') as f:
        for line in f:
            m = pattern.search(line)
            if m:
                path = m.group(1)
                if os.path.isabs(path):
                    try:
                        path = os.path.relpath(path)
                    except ValueError:
                        pass
                files.add(path)
    print(f"  -> {len(files)} C/C++ source files", file=sys.stderr)
    return files


# ── 2. Java / Kotlin from module-info.json ──────────────────────────────

def get_installed_modules(installed_files_path):
    modules = set()
    if not os.path.exists(installed_files_path):
        print(f"[SKIP] {installed_files_path} not found", file=sys.stderr)
        return modules
    print(f"Parsing {installed_files_path} ...", file=sys.stderr)
    with open(installed_files_path, 'r') as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                name = os.path.splitext(os.path.basename(parts[1]))[0]
                modules.add(name)
    print(f"  -> {len(modules)} installed modules", file=sys.stderr)
    return modules


def get_java_files(module_info_path, installed_modules):
    files = set()
    if not os.path.exists(module_info_path):
        print(f"[SKIP] {module_info_path} not found", file=sys.stderr)
        return files
    print(f"Parsing {module_info_path} ...", file=sys.stderr)
    try:
        with open(module_info_path, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[ERROR] {module_info_path}: {e}", file=sys.stderr)
        return files

    matched = 0
    for mod_name, info in data.items():
        # Exact match only
        is_installed = mod_name in installed_modules
        if not is_installed:
            for inst_path in info.get('installed', []):
                if os.path.splitext(os.path.basename(inst_path))[0] in installed_modules:
                    is_installed = True
                    break
        if not is_installed:
            continue
        matched += 1
        for s in info.get('srcs', []):
            files.add(s)
        # path is a directory — walk for source files (never add raw dir)
        for p in info.get('path', []):
            if os.path.isdir(p):
                for root_dir, _, fnames in os.walk(p):
                    for fn in fnames:
                        if os.path.splitext(fn)[1] in JAVA_MODULE_EXTS:
                            files.add(os.path.join(root_dir, fn))

    print(f"  -> {matched} matched modules, {len(files)} source files", file=sys.stderr)
    return files


# ── 3. Platform-agnostic build tracking ─────────────────────────────────

def parse_lunch_target(build_cmd):
    """Extract (product, variant) from any build command containing 'lunch'."""
    m = re.search(r'lunch\s+(\S+)', build_cmd)
    if not m:
        return None, None
    parts = m.group(1).split('-', 1)
    return parts[0], (parts[1] if len(parts) > 1 else 'userdebug')


def find_product_dir(product_name, bsp_root):
    """
    Locate device/<vendor>[/<soc>]/<product> directory.
    Returns (product_dir, vendor_name) or (None, None).
    """
    device_root = os.path.join(bsp_root, 'device')
    if not os.path.isdir(device_root):
        return None, None
    for vendor in os.listdir(device_root):
        vendor_path = os.path.join(device_root, vendor)
        if not os.path.isdir(vendor_path):
            continue
        # Level 1: device/<vendor>/<product>/ (Qualcomm, some MTK)
        candidate = os.path.join(vendor_path, product_name)
        if os.path.isdir(candidate):
            return candidate, vendor
        # Level 2: device/<vendor>/<soc>/<product>/ (Rockchip, some MTK)
        for soc in os.listdir(vendor_path):
            soc_path = os.path.join(vendor_path, soc)
            if not os.path.isdir(soc_path):
                continue
            candidate = os.path.join(soc_path, product_name)
            if os.path.isdir(candidate):
                return candidate, vendor
    return None, None


def find_kernel_dir(bsp_root):
    """Find the kernel source directory (handles kernel/, kernel-X.Y/, bsp/kernel/)."""
    for kd in KERNEL_DIR_CANDIDATES:
        full = os.path.join(bsp_root, kd)
        if os.path.isdir(full) and os.path.exists(os.path.join(full, 'Makefile')):
            return kd
    return 'kernel'  # fallback


def find_dts_file(dts_name, kernel_dir, kernel_arch, bsp_root, vendor_hint=''):
    """
    Search for <dts_name>.dts across all DTS subdirectories.
    Returns (dts_file_path, dts_dir) or (None, None).
    """
    dts_base = os.path.join(bsp_root, kernel_dir, 'arch', kernel_arch, 'boot', 'dts')
    target = f'{dts_name}.dts'

    # Priority 1: vendor-hinted subdirectory
    if vendor_hint:
        vendor_subdir = VENDOR_DTS_MAP.get(vendor_hint, vendor_hint)
        candidate_dir = os.path.join(dts_base, vendor_subdir)
        candidate_file = os.path.join(candidate_dir, target)
        if os.path.exists(candidate_file):
            return candidate_file, candidate_dir

    # Priority 2: scan all subdirectories
    if os.path.isdir(dts_base):
        for subdir in os.listdir(dts_base):
            candidate_dir = os.path.join(dts_base, subdir)
            if not os.path.isdir(candidate_dir):
                continue
            candidate_file = os.path.join(candidate_dir, target)
            if os.path.exists(candidate_file):
                return candidate_file, candidate_dir

    # Priority 3: some vendors put DTS in vendor/ subdirs
    vendor_dts_base = os.path.join(dts_base, 'vendor')
    if os.path.isdir(vendor_dts_base):
        for subdir in os.listdir(vendor_dts_base):
            candidate_dir = os.path.join(vendor_dts_base, subdir)
            candidate_file = os.path.join(candidate_dir, target)
            if os.path.exists(candidate_file):
                return candidate_file, candidate_dir

    return None, None


def parse_makefile_vars(makefile_path, var_names):
    result = {}
    if not os.path.exists(makefile_path):
        return result
    with open(makefile_path, 'r') as f:
        content = f.read()
    for var in var_names:
        m = re.search(rf'^{re.escape(var)}\s*[:?]?=\s*(.+?)$', content, re.MULTILINE)
        if m:
            val = m.group(1).strip()
            val = val.replace('$(LOCAL_PATH)', os.path.dirname(makefile_path))
            result[var] = val
    return result


def collect_makefile_includes(makefile_path, bsp_root):
    result = set()
    if not os.path.exists(makefile_path):
        return result
    result.add(os.path.relpath(makefile_path, bsp_root))
    with open(makefile_path, 'r') as f:
        for line in f:
            line = line.strip()
            inc = re.match(r'-?include\s+(.+)', line)
            inh = re.search(r'inherit-product[^,]*,\s*(.+?)\)', line)
            path = None
            if inc:
                path = inc.group(1).strip()
            elif inh:
                path = inh.group(1).strip()
            if path:
                path = path.replace('$(LOCAL_PATH)', os.path.dirname(makefile_path))
                path = path.replace('$(SRC_TARGET_DIR)', os.path.join(bsp_root, 'build/target'))
                if not os.path.isabs(path):
                    path = os.path.join(bsp_root, path)
                if os.path.exists(path):
                    result.add(os.path.relpath(path, bsp_root))
    return result


def trace_dts_includes(dts_path, dts_dir):
    """Recursively trace #include / /include/ in DTS/DTSI files."""
    result = set()
    if not os.path.exists(dts_path):
        return result
    real = os.path.realpath(dts_path)
    result.add(real)
    try:
        with open(dts_path, 'r') as f:
            content = f.read()
    except IOError:
        return result
    for m in re.finditer(r'(?:#include|/include/)\s*"([^"]+)"', content):
        inc_file = m.group(1)
        candidates = [
            os.path.join(os.path.dirname(dts_path), inc_file),
            os.path.join(dts_dir, inc_file),
            os.path.join(dts_dir, '..', '..', '..', 'include', inc_file),
        ]
        for c in candidates:
            if os.path.exists(c):
                rc = os.path.realpath(c)
                if rc not in result:
                    result.update(trace_dts_includes(c, dts_dir))
                break
    return result


def get_build_tracked_files(build_cmd, bsp_root):
    files = set()
    product, variant = parse_lunch_target(build_cmd)
    if not product:
        print("[WARN] Cannot parse lunch target from build command", file=sys.stderr)
        return files
    print(f"Build tracking: {product}-{variant}", file=sys.stderr)

    # ── locate product dir & detect vendor ──
    product_dir, vendor = find_product_dir(product, bsp_root)
    if not product_dir:
        print(f"[WARN] Product dir for '{product}' not found under device/", file=sys.stderr)
        return files
    print(f"  Product dir: {os.path.relpath(product_dir, bsp_root)}", file=sys.stderr)
    print(f"  Vendor:      {vendor}", file=sys.stderr)

    # All files in product dir
    for root_dir, _, fnames in os.walk(product_dir):
        for fn in fnames:
            files.add(os.path.relpath(os.path.join(root_dir, fn), bsp_root))

    # Makefile include chain
    for mk in globmod.glob(os.path.join(product_dir, '*.mk')):
        files.update(collect_makefile_includes(mk, bsp_root))

    # ── BoardConfig variable chain ──
    board_configs = []
    bc_product = os.path.join(product_dir, 'BoardConfig.mk')
    parent_dir = os.path.dirname(product_dir)
    bc_parent = os.path.join(parent_dir, 'BoardConfig.mk')
    if os.path.exists(bc_parent):
        board_configs.append(bc_parent)
    if os.path.exists(bc_product):
        board_configs.append(bc_product)

    target_vars = {}
    for bc in board_configs:
        target_vars.update(parse_makefile_vars(bc, BOARDCONFIG_VARS))

    # Resolve kernel DTS name (try all known variable names)
    kernel_dts = (target_vars.get('PRODUCT_KERNEL_DTS')
                  or target_vars.get('BOARD_KERNEL_DTS')
                  or target_vars.get('TARGET_KERNEL_DT')
                  or target_vars.get('KERNEL_DTS', ''))
    kernel_config = (target_vars.get('PRODUCT_KERNEL_CONFIG')
                     or target_vars.get('KERNEL_DEFCONFIG')
                     or target_vars.get('TARGET_KERNEL_CONFIG', ''))
    kernel_arch = (target_vars.get('PRODUCT_KERNEL_ARCH')
                   or target_vars.get('TARGET_KERNEL_ARCH', 'arm64'))

    print(f"  KERNEL_DTS:    {kernel_dts}", file=sys.stderr)
    print(f"  KERNEL_CONFIG: {kernel_config}", file=sys.stderr)

    # ── Find kernel directory ──
    kernel_dir = find_kernel_dir(bsp_root)
    print(f"  Kernel dir:    {kernel_dir}", file=sys.stderr)

    # ── DTS include chain ──
    if kernel_dts:
        dts_file, dts_dir = find_dts_file(kernel_dts, kernel_dir, kernel_arch,
                                           bsp_root, vendor_hint=vendor)
        if dts_file:
            chain = trace_dts_includes(dts_file, dts_dir)
            for f in chain:
                files.add(os.path.relpath(f, bsp_root))
            print(f"  -> {len(chain)} DTS/DTSI files in include chain", file=sys.stderr)
        else:
            print(f"  [WARN] DTS '{kernel_dts}.dts' not found in any kernel DTS dir", file=sys.stderr)

    # ── defconfig ──
    if kernel_config:
        defconfig = os.path.join(bsp_root, kernel_dir, 'arch', kernel_arch,
                                 'configs', kernel_config)
        if os.path.exists(defconfig):
            files.add(os.path.relpath(defconfig, bsp_root))

    # ── DTBO template ──
    dtbo = target_vars.get('PRODUCT_DTBO_TEMPLATE', '')
    if dtbo:
        dtbo_abs = dtbo if os.path.isabs(dtbo) else os.path.join(bsp_root, dtbo)
        if os.path.exists(dtbo_abs):
            files.add(os.path.relpath(dtbo_abs, bsp_root))

    # ── vendor common configs (device/<vendor>/common/) ──
    common_dir = os.path.join(bsp_root, 'device', vendor, 'common')
    if os.path.isdir(common_dir):
        for fn in os.listdir(common_dir):
            fp = os.path.join(common_dir, fn)
            if os.path.isfile(fp) and os.path.splitext(fn)[1] in CONFIG_EXTS:
                files.add(os.path.relpath(fp, bsp_root))

    # ── parent SoC-level configs ──
    if os.path.isdir(parent_dir):
        for fn in os.listdir(parent_dir):
            fp = os.path.join(parent_dir, fn)
            if os.path.isfile(fp) and os.path.splitext(fn)[1] in ('.mk', '.cfg', '.xml', '.prop'):
                files.add(os.path.relpath(fp, bsp_root))
        device_mk = os.path.join(parent_dir, 'device.mk')
        if os.path.exists(device_mk):
            files.update(collect_makefile_includes(device_mk, bsp_root))

    # ── root build script ──
    for bs in ['build.sh', 'Makefile']:
        if os.path.exists(os.path.join(bsp_root, bs)):
            files.add(bs)

    print(f"  -> {len(files)} build-tracked files total", file=sys.stderr)
    return files


# ── output filter ───────────────────────────────────────────────────────

def is_source_or_config(filepath):
    """Return True if the path looks like a real source/config file (not a directory)."""
    basename = os.path.basename(filepath)
    ext = os.path.splitext(filepath)[1]
    if ext in SKIP_EXTS:
        return False
    if '.' in basename:
        return True
    # Known extensionless files
    if basename in ('Makefile', 'Kconfig', 'Kbuild', 'OWNERS',
                    'MODULE_LICENSE_APACHE2', 'Android.mk'):
        return True
    if 'defconfig' in basename:
        return True
    return False


# ── main ────────────────────────────────────────────────────────────────

def write_index(lines: list, bsp_root: Path,
                output: Optional[Path] = None) -> Path:
    """Write active index to .codenav/active_files.idx, backing up any existing
    file to active_files.idx.prev first. Returns the path written."""
    out = output or (bsp_root / ".codenav" / "active_files.idx")
    out.parent.mkdir(exist_ok=True)
    if out.exists():
        prev = out.with_suffix(".idx.prev")
        if prev.exists():
            prev.unlink()
        out.rename(prev)
        print(f"INFO: backed up previous index to {prev.name}",
              file=sys.stderr)
    out.write_text("\n".join(lines) + "\n")
    return out


def main():
    parser = argparse.ArgumentParser(
        description='Generate BSP active file index (.codenav/active_files.idx)')
    parser.add_argument('--build-cmd', '-b', type=str, default='',
                        help='Full build command, e.g. '
                             '"source build/envsetup.sh && lunch <product>-userdebug"')
    parser.add_argument('--root', '-r', type=str, default=os.getcwd(),
                        help='BSP root directory (default: cwd)')
    parser.add_argument('--output', type=str, default=None,
                        help='output path (default: $BSP_ROOT/.codenav/active_files.idx). '
                             'Pass "-" for stdout (legacy behavior).')
    args = parser.parse_args()

    bsp_root = os.path.abspath(args.root)
    os.chdir(bsp_root)
    active_files = set()

    # 1. C/C++ from compile_commands.json
    active_files.update(get_cpp_files('compile_commands.json'))

    # 2. Java/Kotlin from module-info + installed-files
    product_out = ""
    if os.path.exists('out/target/product'):
        subdirs = [d for d in os.listdir('out/target/product')
                   if os.path.isdir(os.path.join('out/target/product', d))]
        if subdirs:
            product_out = os.path.join('out/target/product', subdirs[0])

    if product_out:
        print(f"Product out: {product_out}", file=sys.stderr)
        installed = get_installed_modules(os.path.join(product_out, 'installed-files.txt'))
        java_files = get_java_files(os.path.join(product_out, 'module-info.json'), installed)
        active_files.update(java_files)

    # 3. Build-command target tracking
    if args.build_cmd:
        active_files.update(get_build_tracked_files(args.build_cmd, bsp_root))
    else:
        print("[INFO] No --build-cmd given; skipping target-specific tracking.", file=sys.stderr)
        print("[INFO] Use -b to enable DTS/defconfig/device-config tracing.", file=sys.stderr)

    # Finalize the sorted list of real source/config files
    print("Finalizing index ...", file=sys.stderr)
    final = [f for f in sorted(active_files) if is_source_or_config(f)]

    if args.output == '-':
        for f in final:
            print(f)
        print(f"Total: {len(final)} active files written to stdout", file=sys.stderr)
    else:
        out = Path(args.output) if args.output else None
        written = write_index(final, Path(bsp_root), output=out)
        print(f"Total: {len(final)} active files written to {written}", file=sys.stderr)


if __name__ == "__main__":
    main()
