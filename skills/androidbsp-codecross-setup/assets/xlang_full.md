
<!-- BEGIN: androidbsp-codecross-setup v=1 -->
## 跨边界追踪（JNI / AIDL / HIDL / syscall / ioctl）

本段由 `androidbsp-codecross-setup` skill 部署。
**gtags / rg 找单端符号；本段脚本找双端/多端配对。** 遇到跨边界追踪需求时，不要手工
拼 JNI mangling、不要翻 `unistd.h` 找 syscall 号、不要 `rg 'case.*0xc0186201'` 找 ioctl
handler——调下面的脚本。

> **领域知识驱动的多步追踪**（DT compatible、sysfs 回调、Binder service、SELinux 策略、
> kernel 子系统资源、Android property、build 模块、init.rc 触发链等）
> 请用 `androidbsp-domaintrace-setup` 部署的 `domain_find.py`——它们 rg 能搜到，但需要领域知识串联多步。

### 统一入口 `xlang_find.py`

绝大多数情况直接调统一入口即可，它按符号形态自动派发：

```
python3 scripts/xlang_find.py <symbol>
```

#### 自动识别规则

| 符号形态 | 识别示例 | 派发到 |
|---|---|---|
| `I<PascalCase>` | `IBluetoothHal`、`ICameraProvider` | aidl_bridge（自动判 aidl/hidl） |
| `Java_*` | `Java_com_foo_Bar_baz` | jni_bridge --from-c |
| FQCN（带点） | `com.android.Foo.bar` | jni_bridge --from-java |
| 两参：FQCN + method | `com.android.Foo bar` | jni_bridge --from-java |
| `SYS_*` / `__NR_*` / `sys_*` | `SYS_openat` | syscall_trace |
| `*_ioctl` | `binder_ioctl`、`drm_ioctl` | ioctl_trace --handler |
| 全大写宏 | `BINDER_WRITE_READ` | ioctl_trace --macro |
| `/dev/*` | `/dev/binder` | 提示 fops / ioctl handler 搜索 |
| 其他 | 未识别 | 回退 `global -xa` |

#### 显式参数（启发式识别不准时）

```
python3 scripts/xlang_find.py --syscall-nr 56
python3 scripts/xlang_find.py --syscall-nr 0x38
python3 scripts/xlang_find.py --ioctl-cmd 0xc0186201
```

### 场景 → 命令速查

| 用户问法 | 应调用 |
|---|---|
| "ICameraProvider 的实现在哪" | `xlang_find.py ICameraProvider` |
| "这个 native 方法的 C 实现" | `xlang_find.py <FQCN> <method>` |
| "`Java_com_foo_Bar_baz` 对应哪个 Java 类" | `xlang_find.py Java_com_foo_Bar_baz` |
| "IBluetoothHal 全链路" | `xlang_find.py IBluetoothHal` |
| "openat 在 kernel 哪里实现" | `xlang_find.py SYS_openat` |
| "syscall 号 56 是啥" | `xlang_find.py --syscall-nr 56` |
| "ioctl 命令 0xc0186201 对应哪个宏" | `xlang_find.py --ioctl-cmd 0xc0186201` |
| "BINDER_WRITE_READ 在驱动里怎么处理" | `xlang_find.py BINDER_WRITE_READ` |
| "binder_ioctl 的 case 列表" | `xlang_find.py binder_ioctl` |
| "/dev/binder 是哪个驱动" | `xlang_find.py /dev/binder` |

### 直接调用子脚本（进阶）

当需要非默认行为时绕过 xlang_find：

```
# JNI 全量扫描
python3 scripts/jni_bridge.py --scan --out .jni_bridge.idx

# AIDL 手工指定类型（auto 无法判断时）
python3 scripts/aidl_bridge.py -i IFoo --type hidl

# AIDL/HIDL 全量清单
python3 scripts/aidl_bridge.py --scan --out .aidl_bridge.idx

# syscall 按号、强制 arch
python3 scripts/syscall_trace.py --nr 56 --arch arm64

# ioctl 全量宏清单
python3 scripts/ioctl_trace.py --scan --out .ioctl.idx
```

### 输出格式

所有脚本输出 **TSV（tab 分隔）**，便于二次处理：

```
<tag>\t<file>[:line]\t<info>
```

| 脚本 | 常见 tag |
|---|---|
| jni_bridge | `JAVA`、`KOTLIN`、`C` |
| aidl_bridge | `DECL`、`GEN-JAVA`、`GEN-CPP`、`GEN-HEADER`、`IMPL`、`CLIENT` |
| syscall_trace | `USER-WRAPPER`、`SYSCALL-NR`、`KERNEL-ENTRY`、`KERNEL-COMPAT`、`KERNEL-HELPER` |
| ioctl_trace | `MACRO-DEF`、`HANDLER-CASE`、`HANDLER-DEF`、`FOPS-BIND`、`USER-CALL` |

### 降级策略

```
xlang_find 未命中 → 回退 global -xa
global 未命中     → rg 全文
rg 未命中         → 检查符号拼写、确认索引覆盖该目录
syscall_trace 找不到 kernel 根 → 手工 --kernel-root /path/to/kernel
```

### 局限

- **JNI**：重载方法的签名后缀（`__ILjava_lang_String_2`）仅做前缀枚举；动态 `System.loadLibrary` 的运行期分发不追。
- **AIDL/HIDL**：静态分析，不追 Binder 运行时路由；接口继承链只做单跳。
- **syscall**：仅 arm/arm64；`ksys_*` 辅助识别靠启发式，可能漏报。
- **ioctl**：命令号反查按 type+nr+dir 比对，size（`sizeof(T)`）在宏展开时才能确定——T 未知时匹配可能偏多；`case` 列表基于 "handler 所在文件内所有 case"，不做 AST 解析。

### 常见问题

**Q: syscall_trace 报 "kernel 根目录未找到"？**
`compile_commands.json` 里没有 `arch/arm64/` 或 `arch/arm/` 路径。解决：`--kernel-root /path/to/kernel` 手工指定；或重编 kernel 后让 `androidbsp-codeindex-setup` 重建 compdb。

**Q: ioctl_trace --cmd 0x... 没命中？**
三种可能：(1) 宏的 type 不是字符字面量（如 `#define FOO_MAGIC 0x81` 再 `_IOR(FOO_MAGIC, ...)`）；(2) 宏跨多行写的；(3) 传入的 size 与宏里 `sizeof(T)` 实际不一致（脚本只比对 type+nr+dir）。

**Q: AIDL 接口 auto 识别错了？**
`python3 scripts/aidl_bridge.py -i IFoo --type aidl` 或 `--type hidl` 手工指定。

**Q: JNI 重载方法 C 侧多个候选？**
正常——JNI 签名编码只在重载时出现。脚本会列出 `__` 后缀变体，按函数签名自己挑。

<!-- END: androidbsp-codecross-setup -->
