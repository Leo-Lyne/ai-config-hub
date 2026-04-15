#!/usr/bin/env python3
"""
跨边界符号追踪统一入口：自动识别符号形态并派发到对应 bridge/trace 脚本。

用法：
  xlang_find.py <symbol> [<optional second arg>]

识别规则：
  跨语言（JNI/AIDL/HIDL）：
    - I<Upper>... 模式                  -> AIDL/HIDL 接口 -> aidl_bridge.py
    - Java_... 前缀                     -> JNI C 符号    -> jni_bridge.py --from-c
    - FQCN 形式（含点 + 方法段）        -> JNI Java 端   -> jni_bridge.py --from-java
    - 两个参数：<FQCN> <method>         -> JNI Java 端   -> jni_bridge.py --from-java

  跨特权（syscall/ioctl）：
    - SYS_xxx / __NR_xxx / sys_xxx      -> syscall_trace.py --name
    - 纯数字或 0x... 且带 --syscall     -> syscall_trace.py --nr
    - 全大写下划线宏 + 看起来像 _IO     -> ioctl_trace.py --macro
    - /dev/* 或 /proc/* 或 /sys/*       -> 提示 fops / show_store 搜索
    - xxx_ioctl 函数名                   -> ioctl_trace.py --handler

  其他：
    - 纯符号名                          -> 先 gtags，再提示

示例：
  xlang_find.py IBluetoothHal
  xlang_find.py Java_com_android_Foo_bar
  xlang_find.py com.android.Foo.bar
  xlang_find.py SYS_openat
  xlang_find.py BINDER_WRITE_READ
  xlang_find.py binder_ioctl
  xlang_find.py --syscall-nr 56
"""

import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def dispatch(cmd):
    return subprocess.call([sys.executable] + cmd)


def looks_like_aidl_interface(s: str) -> bool:
    return bool(re.match(r'^I[A-Z][A-Za-z0-9_]*$', s))


def looks_like_jni_c(s: str) -> bool:
    return s.startswith('Java_')


def looks_like_syscall_name(s: str):
    """SYS_openat / __NR_openat / sys_openat -> 返回剥掉前缀的名字；否则 None"""
    for pfx in ('SYS_', '__NR_', 'sys_'):
        if s.startswith(pfx) and re.match(r'^\w+$', s[len(pfx):]):
            return s[len(pfx):]
    return None


def looks_like_ioctl_macro(s: str) -> bool:
    """经验式：全大写 + 下划线，长度 >= 4，且不像 AIDL/JNI。"""
    if not re.match(r'^[A-Z][A-Z0-9_]{3,}$', s):
        return False
    # 排除明显不是 ioctl 的
    if s.startswith('SYS_') or s.startswith('__NR_'):
        return False
    return True


def looks_like_ioctl_handler(s: str) -> bool:
    return bool(re.match(r'^[a-z_][a-z0-9_]*_ioctl$', s))


def looks_like_devpath(s: str) -> bool:
    return s.startswith('/dev/') or s.startswith('/proc/') or s.startswith('/sys/')


def split_fqcn_method(s: str):
    if '.' not in s:
        return None
    idx = s.rfind('.')
    fqcn = s[:idx]; method = s[idx+1:]
    if re.match(r'^[\w.]+$', fqcn) and re.match(r'^\w+$', method):
        if '.' in fqcn:
            return fqcn, method
    return None


def hint_devpath(path: str):
    """对 /dev/foo 之类给出搜索提示而非直接派发。"""
    print(f'# 设备/虚拟文件路径：{path}', file=sys.stderr)
    if path.startswith('/dev/'):
        dev = path[len('/dev/'):]
        print(f'# 建议：', file=sys.stderr)
        print(f'#   rg -n \'device_create.*"{dev}"|class_create.*"{dev}"|"{dev}"\' -g "*.c"',
              file=sys.stderr)
        print(f'#   随后 xlang_find.py <drv>_ioctl 找 handler', file=sys.stderr)
    elif path.startswith('/sys/'):
        leaf = path.rstrip('/').split('/')[-1]
        print(f'# 建议：', file=sys.stderr)
        print(f'#   rg -n \'DEVICE_ATTR\\w*\\({leaf}\\b|"{leaf}"\' -g "*.c"',
              file=sys.stderr)
    elif path.startswith('/proc/'):
        leaf = path.rstrip('/').split('/')[-1]
        print(f'# 建议：', file=sys.stderr)
        print(f'#   rg -n \'proc_create\\w*\\([^,]*"{leaf}"\' -g "*.c"', file=sys.stderr)


def main():
    if len(sys.argv) == 1 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)

    root_args = []
    if '--root' in sys.argv:
        i = sys.argv.index('--root')
        if i + 1 < len(sys.argv):
            root_args = ['--root', sys.argv[i+1]]
            del sys.argv[i:i+2]

    # 显式 --syscall-nr N
    if '--syscall-nr' in sys.argv:
        i = sys.argv.index('--syscall-nr')
        if i + 1 < len(sys.argv):
            return dispatch([str(SCRIPT_DIR / 'syscall_trace.py'),
                             '--nr', sys.argv[i+1]] + root_args)

    # 显式 --ioctl-cmd 0x...
    if '--ioctl-cmd' in sys.argv:
        i = sys.argv.index('--ioctl-cmd')
        if i + 1 < len(sys.argv):
            return dispatch([str(SCRIPT_DIR / 'ioctl_trace.py'),
                             '--cmd', sys.argv[i+1]] + root_args)

    argv = [a for a in sys.argv[1:] if not a.startswith('--')]

    # 两个位置参数：FQCN + method
    if len(argv) == 2 and '.' in argv[0] and re.match(r'^\w+$', argv[1]):
        return dispatch([str(SCRIPT_DIR / 'jni_bridge.py'),
                         '--from-java', argv[0], argv[1]] + root_args)

    if len(argv) != 1:
        print(f'unexpected args: {argv}', file=sys.stderr)
        print(__doc__)
        sys.exit(1)

    sym = argv[0]

    # 顺序很重要：更具体的模式先判
    if looks_like_jni_c(sym):
        return dispatch([str(SCRIPT_DIR / 'jni_bridge.py'),
                         '--from-c', sym] + root_args)

    if looks_like_aidl_interface(sym):
        return dispatch([str(SCRIPT_DIR / 'aidl_bridge.py'),
                         '--interface', sym] + root_args)

    sc = looks_like_syscall_name(sym)
    if sc is not None:
        return dispatch([str(SCRIPT_DIR / 'syscall_trace.py'), sc] + root_args)

    if looks_like_ioctl_handler(sym):
        return dispatch([str(SCRIPT_DIR / 'ioctl_trace.py'),
                         '--handler', sym] + root_args)

    if looks_like_devpath(sym):
        hint_devpath(sym)
        return 0

    split = split_fqcn_method(sym)
    if split:
        fqcn, method = split
        return dispatch([str(SCRIPT_DIR / 'jni_bridge.py'),
                         '--from-java', fqcn, method] + root_args)

    if looks_like_ioctl_macro(sym):
        return dispatch([str(SCRIPT_DIR / 'ioctl_trace.py'),
                         '--macro', sym] + root_args)

    # 退路：gtags 普通符号查找
    print(f'# 无法识别符号形态，退回 gtags 通用查找：{sym}', file=sys.stderr)
    rc = subprocess.call(['global', '-xa', sym])
    if rc != 0:
        print(f'# gtags 未命中；建议：', file=sys.stderr)
        print(f'#   rg -n "{sym}"', file=sys.stderr)
    return rc


if __name__ == '__main__':
    sys.exit(main())
