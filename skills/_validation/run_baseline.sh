#!/usr/bin/env bash
# Run 24 baseline queries on a deployed BSP, capture outputs for Phase 0/N comparison.
#
# Usage: run_baseline.sh <bsp_root> <out_dir> [script_dir]
#   bsp_root    - absolute path to BSP root
#   out_dir     - where to save per-query .txt outputs
#   script_dir  - dir under bsp_root containing the .py scripts (default: scripts)
#                 Phase 0 uses "scripts"; Phase N uses ".codenav/scripts"
set -uo pipefail
BSP_ROOT="${1:?usage: $0 <bsp_root> <out_dir> [script_dir]}"
OUT_DIR_ARG="${2:?usage: $0 <bsp_root> <out_dir> [script_dir]}"
SCRIPT_DIR="${3:-scripts}"

mkdir -p "$OUT_DIR_ARG"
OUT_DIR="$(realpath "$OUT_DIR_ARG")"
BSP_ROOT="$(realpath "$BSP_ROOT")"
cd "$BSP_ROOT"

run_query() {
  local id="$1" cmd="$2" desc="$3"
  local out="$OUT_DIR/${id}.txt"
  local stderr_tmp="$(mktemp)"
  local t0 t1 rc
  t0=$(date +%s.%N)
  local stdout
  stdout=$(eval "$cmd" 2> "$stderr_tmp")
  rc=$?
  t1=$(date +%s.%N)
  {
    echo "=== ID: $id"
    echo "=== DESC: $desc"
    echo "=== CMD: $cmd"
    echo "=== START: $(date -Iseconds)"
    echo "=== EXIT: $rc"
    echo "=== ELAPSED: $(echo "$t1 - $t0" | bc)s"
    echo "=== STDERR:"
    cat "$stderr_tmp"
    echo "=== STDOUT:"
    echo "$stdout"
  } > "$out"
  rm -f "$stderr_tmp"
  printf "  [%s] exit=%d lines=%d desc=%s\n" "$id" "$rc" \
    "$(echo "$stdout" | wc -l)" "$desc"
}

D="$SCRIPT_DIR"

run_query 01 "bash $D/arg.sh 'rockchip_pcie_probe' 2>&1" 'arg pcie probe (expect ≥1)'
run_query 02 "bash $D/arg.sh 'definitely_nonexistent_symbol_xyz123' 2>&1" 'arg empty (expect 0 hits, exit 0)'
run_query 03 "global -r printk 2>/dev/null | head -200" 'gtags printk refs (huge, no hang)'
run_query 04 "python3 $D/jni_bridge.py --from-c JNI_OnLoad 2>&1" 'jni framework entries'
run_query 05 "python3 $D/aidl_bridge.py --interface ICameraProvider 2>&1" 'HIDL camera provider'
run_query 06 "python3 $D/aidl_bridge.py --interface IRadio 2>&1" 'AIDL Radio path'
run_query 07 "python3 $D/syscall_trace.py openat 2>&1" 'syscall both sides'
run_query 08 "python3 $D/ioctl_trace.py --macro BINDER_WRITE_READ 2>&1" 'binder ioctl'
run_query 09 "python3 $D/dt_bind.py --compatible rockchip,rk3568-pcie 2>&1" 'DT both sides'
run_query 10 "python3 $D/sysfs_attr.py --attr current_temp 2>&1" 'thermal attr'
run_query 11 "python3 $D/binder_svc.py --service android.hardware.camera.provider 2>&1" 'svc registration'
run_query 12 "python3 $D/binder_svc.py --hal ICameraProvider 2>&1" 'svc by interface name'
run_query 13 "python3 $D/selinux_trace.py --domain untrusted_app 2>&1" 'te + contexts'
run_query 14 "python3 $D/subsys_trace.py --clock clk_pcie_aux 2>&1" 'clock prov/cons/DT'
run_query 15 "python3 $D/prop_trace.py --property ro.product.model 2>&1" 'prop multi-source'
run_query 16 "python3 $D/prop_trace.py --property ro.vendor.region 2>&1" 'prop multi-partition'
run_query 17 "python3 $D/build_trace.py --module libbinder 2>&1" 'bp + install'
run_query 18 "python3 $D/initrc_trace.py --service vendor.power.stats 2>&1" 'init trigger+service'
run_query 19 "python3 $D/kconfig_trace.py --config CONFIG_DRM_ROCKCHIP 2>&1" 'kconfig defconfig+ifdef'
run_query 20 "python3 $D/firmware_trace.py --firmware rk_tb_8852be_fw.bin 2>&1" 'firmware request+pkg'
run_query 21 "python3 $D/netlink_trace.py --family NL80211 2>&1" 'netlink family+userspace'
run_query 22 "python3 $D/media_topo.py --subdev rkisp 2>&1" 'V4L2 subdev+pad'
run_query 23 "python3 $D/xlang_find.py openat 2>&1" 'xlang dispatcher to syscall'
run_query 24 "python3 $D/domain_find.py rockchip,rk3568-pcie 2>&1" 'domain dispatcher to dt'

echo
echo "Done. 24 outputs in $OUT_DIR"
