#!/usr/bin/env python3
"""
领域知识驱动的多步追踪统一入口：自动识别符号形态并派发到对应 trace 脚本。

与 codecross 的 xlang_find.py 互补：
  - xlang_find 处理"rg 搜不到对面"的跨边界（JNI mangling、syscall 编码、ioctl 命令号）
  - domain_find 处理"rg 能搜到但需要领域知识串联多步"的追踪

用法：
  domain_find.py <symbol>

自动识别规则：

  硬件描述（DT / sysfs）：
    - "vendor,xxx" 形式            -> dt_bind --compatible
    - xxx_driver                    -> dt_bind --driver
    - /sys/*                        -> sysfs_attr --attr（叶节点）
    - /proc/*                       -> sysfs_attr --proc（叶节点）
    - xxx_show / xxx_store          -> sysfs_attr --callback

  服务/进程（Binder / VINTF）：
    - android.hardware.xxx          -> binder_svc --hal
    - hal_xxx / xxx_default 等      -> selinux_trace --domain

  安全策略（SELinux）：
    - /dev/*                        -> selinux_trace --device
    - avc: denied ...               -> selinux_trace --avc

  Android Property：
    - ro.xxx / persist.xxx / sys.xxx 等 -> prop_trace --property

  Init .rc：
    - on property:xxx=yyy 形式      -> initrc_trace --trigger

  Build 系统：
    - lib*.so                       -> build_trace --so

  Kernel 子系统：
    - CONFIG_XXX                    -> kconfig_trace --config
    - *.fw / *.bin (firmware)       -> firmware_trace --firmware

显式参数（绕过启发式）：
  # DT
  domain_find.py --compatible "qcom,camera-sensor"
  domain_find.py --driver imx219_driver
  domain_find.py --dt-property clock-frequency
  domain_find.py --overlay camera

  # sysfs/procfs/debugfs
  domain_find.py --sysfs brightness
  domain_find.py --proc interrupts
  domain_find.py --debugfs regmap
  domain_find.py --callback brightness_store

  # Binder/VINTF
  domain_find.py --service camera.provider
  domain_find.py --process cameraserver
  domain_find.py --hal android.hardware.camera.provider

  # SELinux
  domain_find.py --avc 'avc: denied { read } ...'
  domain_find.py --domain hal_camera_default
  domain_find.py --device /dev/video0
  domain_find.py --se-type sysfs_camera
  domain_find.py --service-context camera.provider

  # Kernel 子系统资源
  domain_find.py --clock xclk
  domain_find.py --regulator vdd
  domain_find.py --gpio reset
  domain_find.py --irq vblank
  domain_find.py --power-domain gpu

  # Android Property / init.rc
  domain_find.py --property ro.hardware.chipname
  domain_find.py --trigger "sys.usb.config=mtp"
  domain_find.py --rc-service cameraserver
  domain_find.py --usb-gadget mtp

  # Build 系统
  domain_find.py --module camera.provider
  domain_find.py --so libcamera_provider.so
  domain_find.py --vndk libutils

  # Kconfig
  domain_find.py --config CONFIG_VIDEO_IMX219

  # Firmware / kernel module
  domain_find.py --firmware imx219.fw
  domain_find.py --ko imx219

  # Netlink
  domain_find.py --netlink nl80211

  # V4L2 / Media
  domain_find.py --subdev imx219
  domain_find.py --media-port csi
"""

from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _bsp_common import make_parser, require_version

require_version("1.0.0")

SCRIPT_DIR = Path(__file__).resolve().parent


# ── 识别函数 ──

def looks_like_dt_compatible(s: str) -> bool:
    return bool(re.match(r'^[a-z][a-z0-9]*,[a-z0-9][\w.-]*$', s))


def looks_like_driver_struct(s: str) -> bool:
    return bool(re.match(r'^[a-z][a-z0-9_]*_driver$', s))


def looks_like_sysfs_callback(s: str):
    return bool(re.match(r'^[a-z][a-z0-9_]+_(show|store)$', s))


def looks_like_hal_fqdn(s: str) -> bool:
    return bool(re.match(r'^(android|vendor|com)\.\w+(\.\w+){1,}$', s)) \
        and not s.endswith('.java') and not s.endswith('.kt')


def looks_like_selinux_domain(s: str) -> bool:
    if not re.match(r'^[a-z][a-z0-9_]+$', s):
        return False
    return any(kw in s for kw in ('_default', 'hal_', '_exec', 'vendor_', 'system_', 'untrusted_'))


def looks_like_android_property(s: str) -> bool:
    """ro.xxx / persist.xxx / sys.xxx / gsm.xxx / init.svc.xxx 等。"""
    return bool(re.match(r'^(ro|persist|sys|gsm|init|debug|dalvik|vendor|ctl)\.\w', s))


def looks_like_kconfig(s: str) -> bool:
    return bool(re.match(r'^CONFIG_[A-Z][A-Z0-9_]+$', s))


def looks_like_firmware(s: str) -> bool:
    return bool(re.match(r'^[\w.-]+\.(fw|bin|img|elf|mdt|mbn|b\d+)$', s))


def looks_like_so(s: str) -> bool:
    return bool(re.match(r'^lib[\w.-]+\.so(\.\d+)*$', s))


# ── 显式 flag → (target_script, target_arg) ──

EXPLICIT_MAP = {
    # dt_bind
    '--compatible':      ('dt_bind.py', '--compatible'),
    '--driver':          ('dt_bind.py', '--driver'),
    '--dt-property':     ('dt_bind.py', '--property'),
    '--overlay':         ('dt_bind.py', '--overlay'),
    # sysfs_attr
    '--sysfs':           ('sysfs_attr.py', '--attr'),
    '--proc':            ('sysfs_attr.py', '--proc'),
    '--debugfs':         ('sysfs_attr.py', '--debugfs'),
    '--callback':        ('sysfs_attr.py', '--callback'),
    # binder_svc
    '--service':         ('binder_svc.py', '--service'),
    '--process':         ('binder_svc.py', '--process'),
    '--hal':             ('binder_svc.py', '--hal'),
    # selinux_trace
    '--avc':             ('selinux_trace.py', '--avc'),
    '--domain':          ('selinux_trace.py', '--domain'),
    '--device':          ('selinux_trace.py', '--device'),
    '--se-type':         ('selinux_trace.py', '--type'),
    '--service-context': ('selinux_trace.py', '--service-context'),
    # subsys_trace
    '--clock':           ('subsys_trace.py', '--clock'),
    '--regulator':       ('subsys_trace.py', '--regulator'),
    '--gpio':            ('subsys_trace.py', '--gpio'),
    '--irq':             ('subsys_trace.py', '--irq'),
    '--power-domain':    ('subsys_trace.py', '--power-domain'),
    # prop_trace
    '--property':        ('prop_trace.py', '--property'),
    # initrc_trace
    '--trigger':         ('initrc_trace.py', '--trigger'),
    '--rc-service':      ('initrc_trace.py', '--service'),
    '--usb-gadget':      ('initrc_trace.py', '--usb-gadget'),
    # build_trace
    '--module':          ('build_trace.py', '--module'),
    '--so':              ('build_trace.py', '--so'),
    '--vndk':            ('build_trace.py', '--vndk'),
    # kconfig_trace
    '--config':          ('kconfig_trace.py', '--config'),
    # firmware_trace
    '--firmware':        ('firmware_trace.py', '--firmware'),
    '--ko':              ('firmware_trace.py', '--ko'),
    # netlink_trace
    '--netlink':         ('netlink_trace.py', '--family'),
    # media_topo
    '--subdev':          ('media_topo.py', '--subdev'),
    '--media-port':      ('media_topo.py', '--port'),
}


def _common_flags(args) -> list:
    """把 Emitter 通用 flag 传给子脚本。"""
    out = []
    if getattr(args, 'json', False):
        out.append('--json')
    if getattr(args, 'no_events', False):
        out.append('--no-events')
    if getattr(args, 'root', None):
        out += ['--root', str(args.root)]
    if getattr(args, 'timeout', None):
        out += ['--timeout', str(args.timeout)]
    return out


def dispatch(target: str, target_arg: str, value: str, args) -> int:
    cmd = [sys.executable, str(SCRIPT_DIR / target), target_arg, value]
    cmd += _common_flags(args)
    return subprocess.call(cmd)


def main():
    if len(sys.argv) == 1 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    # 允许显式参数先解析（手动，因为我们的显式表用的是自定义 flag）
    # 做法：先把任何匹配 EXPLICIT_MAP 的 --flag VALUE 对挑出来，剩下给 argparse 处理
    raw = sys.argv[1:]
    explicit_hit = None
    remaining = []
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok in EXPLICIT_MAP and i + 1 < len(raw):
            explicit_hit = (EXPLICIT_MAP[tok], raw[i + 1])
            i += 2
            continue
        remaining.append(tok)
        i += 1

    # 恢复 sys.argv 让 argparse 仅看到通用 flag + 可能的 positional sym
    sys.argv = [sys.argv[0]] + remaining
    p = make_parser('domaintrace dispatcher')
    p.add_argument('symbol', nargs='?')
    args = p.parse_args()

    if explicit_hit:
        (script, target_arg), value = explicit_hit
        return dispatch(script, target_arg, value, args)

    sym = args.symbol
    if not sym:
        print(__doc__)
        sys.exit(1)

    # avc denied 日志
    if 'avc:' in sym or 'avc: denied' in sym:
        return dispatch('selinux_trace.py', '--avc', sym, args)

    # CONFIG_XXX
    if looks_like_kconfig(sym):
        return dispatch('kconfig_trace.py', '--config', sym, args)

    # DT compatible: "vendor,foo-bar"
    if looks_like_dt_compatible(sym):
        return dispatch('dt_bind.py', '--compatible', sym, args)

    # driver struct: xxx_driver
    if looks_like_driver_struct(sym):
        return dispatch('dt_bind.py', '--driver', sym, args)

    # HAL FQDN: android.hardware.xxx.yyy
    if looks_like_hal_fqdn(sym):
        return dispatch('binder_svc.py', '--hal', sym, args)

    # Android property: ro.xxx / persist.xxx / sys.xxx
    if looks_like_android_property(sym):
        return dispatch('prop_trace.py', '--property', sym, args)

    # /dev/* → selinux_trace --device
    if sym.startswith('/dev/'):
        return dispatch('selinux_trace.py', '--device', sym, args)

    # /sys/* → sysfs_attr --attr (叶节点名)
    if sym.startswith('/sys/'):
        leaf = sym.rstrip('/').split('/')[-1]
        return dispatch('sysfs_attr.py', '--attr', leaf, args)

    # /proc/* → sysfs_attr --proc (叶节点名)
    if sym.startswith('/proc/'):
        leaf = sym.rstrip('/').split('/')[-1]
        return dispatch('sysfs_attr.py', '--proc', leaf, args)

    # firmware 文件: xxx.fw / xxx.bin
    if looks_like_firmware(sym):
        return dispatch('firmware_trace.py', '--firmware', sym, args)

    # .so 文件: libfoo.so
    if looks_like_so(sym):
        return dispatch('build_trace.py', '--so', sym, args)

    # sysfs 回调: xxx_show / xxx_store
    if looks_like_sysfs_callback(sym):
        return dispatch('sysfs_attr.py', '--callback', sym, args)

    # on property:xxx=yyy 形式
    if sym.startswith('property:') or ('=' in sym and looks_like_android_property(sym.split('=')[0])):
        trigger = sym.removeprefix('property:')
        return dispatch('initrc_trace.py', '--trigger', trigger, args)

    # SELinux domain（启发式：含 hal_、_default 等标志）
    if looks_like_selinux_domain(sym):
        return dispatch('selinux_trace.py', '--domain', sym, args)

    # 未识别 → 提示
    print(f'# 无法识别符号形态：{sym}', file=sys.stderr)
    print(f'# 如果是跨语言/跨特权边界（JNI/AIDL/syscall/ioctl），请用 xlang_find.py', file=sys.stderr)
    print(f'# 否则尝试显式参数：domain_find.py --help', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
