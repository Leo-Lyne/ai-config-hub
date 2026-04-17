
<!-- BEGIN: androidbsp-domaintrace-setup v=1 -->
## 领域追踪（DT / sysfs / Binder / SELinux / 子系统资源 / Property / Build / init.rc / Kconfig / Firmware / Netlink / V4L2）

本段由 `androidbsp-domaintrace-setup` skill 部署。
**rg 能搜到，但需要领域知识决定搜什么、串联哪些步骤。** 遇到下列追踪需求时调对应脚本。

跨语言/跨特权边界（JNI / AIDL / HIDL / syscall / ioctl）请用 codecross 的 `xlang_find.py`。

### 统一入口 `domain_find.py`

```
python3 scripts/domain_find.py <symbol>
```

#### 自动识别规则

| 符号形态 | 识别示例 | 派发到 |
|---|---|---|
| `vendor,xxx` 形式 | `qcom,camera-sensor` | dt_bind --compatible |
| `*_driver` | `imx219_driver` | dt_bind --driver |
| `android.hardware.*` | `android.hardware.camera.provider` | binder_svc --hal |
| `/dev/*` | `/dev/video0` | selinux_trace --device |
| `/sys/*` | `/sys/class/leds/brightness` | sysfs_attr --attr（叶节点） |
| `/proc/*` | `/proc/interrupts` | sysfs_attr --proc（叶节点） |
| `*_show` / `*_store` | `brightness_store` | sysfs_attr --callback |
| `hal_*` / `*_default` 等 | `hal_camera_default` | selinux_trace --domain |
| `ro.*` / `persist.*` / `sys.*` | `ro.hardware.chipname` | prop_trace --property |
| `CONFIG_*` | `CONFIG_VIDEO_IMX219` | kconfig_trace --config |
| `*.fw` / `*.bin` | `venus.mdt` | firmware_trace --firmware |
| `lib*.so` | `libcamera_provider.so` | build_trace --so |
| `property:X=Y` 形式 | `sys.usb.config=mtp` | initrc_trace --trigger |

#### 显式参数（启发式识别不准时）

```
# DT / Overlay
python3 scripts/domain_find.py --compatible "qcom,camera-sensor"
python3 scripts/domain_find.py --driver imx219_driver
python3 scripts/domain_find.py --dt-property clock-frequency
python3 scripts/domain_find.py --overlay camera

# sysfs / procfs / debugfs
python3 scripts/domain_find.py --sysfs brightness
python3 scripts/domain_find.py --proc interrupts
python3 scripts/domain_find.py --debugfs regmap
python3 scripts/domain_find.py --callback brightness_store

# Binder / VINTF
python3 scripts/domain_find.py --service camera.provider
python3 scripts/domain_find.py --process cameraserver
python3 scripts/domain_find.py --hal android.hardware.camera.provider

# SELinux
python3 scripts/domain_find.py --avc 'avc: denied { read } for ...'
python3 scripts/domain_find.py --domain hal_camera_default
python3 scripts/domain_find.py --device /dev/video0
python3 scripts/domain_find.py --se-type sysfs_camera
python3 scripts/domain_find.py --service-context camera.provider

# Kernel 子系统资源（clock / regulator / GPIO / IRQ / power-domain）
python3 scripts/domain_find.py --clock xclk
python3 scripts/domain_find.py --regulator vdd
python3 scripts/domain_find.py --gpio reset
python3 scripts/domain_find.py --irq vblank
python3 scripts/domain_find.py --power-domain gpu

# Android Property / init.rc
python3 scripts/domain_find.py --property ro.hardware.chipname
python3 scripts/domain_find.py --trigger "sys.usb.config=mtp"
python3 scripts/domain_find.py --rc-service cameraserver
python3 scripts/domain_find.py --usb-gadget mtp

# Build 系统 / VNDK
python3 scripts/domain_find.py --module camera.provider
python3 scripts/domain_find.py --so libcamera_provider.so
python3 scripts/domain_find.py --vndk libutils

# Kconfig
python3 scripts/domain_find.py --config CONFIG_VIDEO_IMX219

# Firmware / kernel module
python3 scripts/domain_find.py --firmware venus.mdt
python3 scripts/domain_find.py --ko imx219

# Netlink
python3 scripts/domain_find.py --netlink nl80211

# V4L2 / Media Controller
python3 scripts/domain_find.py --subdev imx219
python3 scripts/domain_find.py --media-port csi
```

### 场景 → 命令速查

| 用户问法 | 应调用 |
|---|---|
| "qcom,camera-sensor 对应哪个 driver" | `domain_find.py qcom,camera-sensor` |
| "imx219_driver 匹配哪些 DTS 节点" | `domain_find.py imx219_driver` |
| "clock-frequency 这个 DT property 谁在读" | `domain_find.py --dt-property clock-frequency` |
| "这个 overlay 改了 camera 节点什么" | `domain_find.py --overlay camera` |
| "/sys/class/leds/brightness 的回调" | `domain_find.py /sys/class/leds/brightness` |
| "brightness_store 函数绑在哪个节点" | `domain_find.py brightness_store` |
| "/proc/interrupts 怎么实现的" | `domain_find.py /proc/interrupts` |
| "camera.provider service 注册在哪" | `domain_find.py --service camera.provider` |
| "android.hardware.camera.provider 的 VINTF" | `domain_find.py android.hardware.camera.provider` |
| "cameraserver 进程跑哪些 service" | `binder_svc.py --process cameraserver` |
| "avc denied 该改哪个 .te" | `domain_find.py --avc '<整行日志>'` |
| "hal_camera_default 的 SELinux 策略" | `domain_find.py hal_camera_default` |
| "/dev/video0 的 SELinux label" | `domain_find.py /dev/video0` |
| "xclk 这个 clock 从哪来" | `domain_find.py --clock xclk` |
| "vdd regulator 的 provider 和 consumer" | `domain_find.py --regulator vdd` |
| "reset GPIO 谁在用" | `domain_find.py --gpio reset` |
| "vblank 中断注册在哪" | `domain_find.py --irq vblank` |
| "gpu power domain 的 provider" | `domain_find.py --power-domain gpu` |
| "ro.hardware.chipname 谁在读写" | `domain_find.py ro.hardware.chipname` |
| "sys.usb.config=mtp 触发了什么" | `domain_find.py --trigger "sys.usb.config=mtp"` |
| "cameraserver 服务什么条件启动" | `domain_find.py --rc-service cameraserver` |
| "mtp USB function 对应的 driver" | `domain_find.py --usb-gadget mtp` |
| "camera.provider 模块定义在哪" | `domain_find.py --module camera.provider` |
| "libcamera_provider.so 是哪个模块编的" | `domain_find.py libcamera_provider.so` |
| "libutils 的 VNDK 可见性" | `domain_find.py --vndk libutils` |
| "CONFIG_VIDEO_IMX219 影响哪些代码" | `domain_find.py CONFIG_VIDEO_IMX219` |
| "venus.mdt 固件从哪加载" | `domain_find.py venus.mdt` |
| "imx219 内核模块怎么编的" | `domain_find.py --ko imx219` |
| "nl80211 family 的 kernel 注册" | `domain_find.py --netlink nl80211` |
| "imx219 V4L2 subdev 拓扑" | `domain_find.py --subdev imx219` |

### 直接调用子脚本（进阶）

当需要非默认行为时绕过 domain_find：

```
# DT
python3 scripts/dt_bind.py --driver imx219_driver
python3 scripts/dt_bind.py --property clock-frequency
python3 scripts/dt_bind.py --overlay camera
python3 scripts/dt_bind.py --scan --out .dt_bind.idx

# sysfs / procfs / debugfs
python3 scripts/sysfs_attr.py --attr brightness
python3 scripts/sysfs_attr.py --callback brightness_store
python3 scripts/sysfs_attr.py --proc interrupts
python3 scripts/sysfs_attr.py --debugfs regmap
python3 scripts/sysfs_attr.py --scan --out .sysfs_attr.idx

# Binder
python3 scripts/binder_svc.py --service camera.provider
python3 scripts/binder_svc.py --process cameraserver
python3 scripts/binder_svc.py --hal android.hardware.camera.provider
python3 scripts/binder_svc.py --scan --out .binder_svc.idx

# SELinux
python3 scripts/selinux_trace.py --avc 'avc: denied { read } for ...'
python3 scripts/selinux_trace.py --domain hal_camera_default
python3 scripts/selinux_trace.py --device /dev/video0
python3 scripts/selinux_trace.py --type sysfs_camera
python3 scripts/selinux_trace.py --service-context camera.provider
python3 scripts/selinux_trace.py --scan --out .selinux.idx

# Kernel 子系统资源
python3 scripts/subsys_trace.py --clock xclk
python3 scripts/subsys_trace.py --regulator vdd
python3 scripts/subsys_trace.py --gpio reset
python3 scripts/subsys_trace.py --irq vblank
python3 scripts/subsys_trace.py --power-domain gpu
python3 scripts/subsys_trace.py --scan --out .subsys.idx

# Android Property
python3 scripts/prop_trace.py --property ro.hardware.chipname
python3 scripts/prop_trace.py --scan --out .prop.idx

# Build 系统
python3 scripts/build_trace.py --module camera.provider
python3 scripts/build_trace.py --so libcamera_provider.so
python3 scripts/build_trace.py --vndk libutils
python3 scripts/build_trace.py --scan --out .build.idx

# init.rc
python3 scripts/initrc_trace.py --trigger "sys.usb.config=mtp"
python3 scripts/initrc_trace.py --service cameraserver
python3 scripts/initrc_trace.py --action boot
python3 scripts/initrc_trace.py --usb-gadget mtp
python3 scripts/initrc_trace.py --scan --out .initrc.idx

# Kconfig
python3 scripts/kconfig_trace.py --config CONFIG_VIDEO_IMX219
python3 scripts/kconfig_trace.py --scan --defconfig arch/arm64/configs/vendor_defconfig --out .kconfig.idx

# Firmware / kernel module
python3 scripts/firmware_trace.py --firmware venus.mdt
python3 scripts/firmware_trace.py --ko imx219
python3 scripts/firmware_trace.py --module-alias "of:N*T*Cvendor,foo*"
python3 scripts/firmware_trace.py --scan --out .firmware.idx

# Netlink
python3 scripts/netlink_trace.py --family nl80211
python3 scripts/netlink_trace.py --scan --out .netlink.idx

# V4L2 / Media Controller
python3 scripts/media_topo.py --subdev imx219
python3 scripts/media_topo.py --entity "imx219 0-001a"
python3 scripts/media_topo.py --port csi
python3 scripts/media_topo.py --scan --out .media_topo.idx
```

### 输出格式

所有脚本输出 **TSV（tab 分隔）**，便于二次处理：

```
<tag>\t<file>[:line]\t<info>
```

| 脚本 | 常见 tag |
|---|---|
| dt_bind | `DTS-NODE`、`DRIVER-COMPAT`、`PROBE-BIND`、`MODULE-REG`、`DRIVER-DEF`、`PROP-READ`、`DTS-PROP`、`DT-BINDING-DOC`、`DTBO-TARGET-PATH`、`DTBO-TARGET-REF`、`DTBO-PROP`、`DT-BASE-NODE` |
| sysfs_attr | `ATTR-DEF`、`SHOW-FUNC`、`STORE-FUNC`、`PROC-CREATE`、`PROC-SEQ`、`DEBUGFS-CREATE`、`FOPS-DEF`、`CALLBACK-DEF`、`ATTR-BIND`、`FOPS-BIND` |
| binder_svc | `SVC-REGISTER`、`SVC-GET`、`RC-SERVICE`、`RC-INTERFACE`、`VINTF`、`SVC-CONTEXT`、`BUILD-DEF`、`AIDL-PACKAGE`、`HAL-REF` |
| selinux_trace | `SUGGEST-ALLOW`、`TE-FILE`、`TYPE-DECL`、`ALLOW-RULE`、`DOMAIN-TRANS`、`RC-SECLABEL`、`FILE-CONTEXT`、`GENFS-CONTEXT`、`SVC-CONTEXT`、`HWSVC-CONTEXT`、`CONTEXT-REF` |
| subsys_trace | `CLK-CONSUMER`、`CLK-DT-NAME`、`CLK-PROVIDER`、`CLK-OF-DECLARE`、`REG-CONSUMER`、`REG-PROVIDER`、`REG-DT-SUPPLY`、`REG-DT-NAME`、`GPIO-CONSUMER`、`GPIO-DT`、`GPIO-CHIP`、`IRQ-REQUEST`、`IRQ-GET`、`IRQ-DT-NAME`、`IRQ-DOMAIN`、`PD-PROVIDER`、`PD-INIT`、`PD-DT-NAME`、`PD-CONSUMER` |
| prop_trace | `PROP-JAVA-GET`、`PROP-JAVA-SET`、`PROP-NATIVE`、`PROP-DEFAULT`、`PROP-BUILD`、`PROP-SECONTEXT`、`PROP-RC-TRIGGER`、`PROP-RC-SET`、`PROP-RC-GET` |
| build_trace | `BP-MODULE`、`BP-TYPE`、`MK-MODULE`、`MK-INSTALL-PATH`、`PRODUCT-PKG`、`BP-DEP`、`VNDK-VENDOR`、`VNDK-ENABLED`、`BP-STEM`、`BUILD-REF` |
| initrc_trace | `RC-TRIGGER`、`RC-START`、`RC-ACTION`、`RC-SERVICE`、`RC-PROP`、`RC-START-BY`、`RC-STOP-BY`、`RC-ENCLOSING-TRIGGER`、`RC-SETPROP`、`USB-RC-CONFIG`、`USB-RC-TRIGGER`、`USB-KERNEL-FUNC`、`USB-KERNEL-NAME` |
| kconfig_trace | `DEFCONFIG`、`DOT-CONFIG`、`KCONFIG-DEF`、`KCONFIG-REF`、`MAKEFILE-OBJ`、`MAKEFILE-REF`、`CODE-IFDEF`、`CODE-IS-ENABLED`、`CODE-IS-BUILTIN`、`CODE-IS-MODULE` |
| firmware_trace | `FW-REQUEST`、`FW-MODULE`、`FW-BUILD`、`FW-COPY`、`FW-PATH`、`KO-MAKEFILE`、`KO-CONFIG`、`KO-DEVICE-TABLE`、`KO-ALIAS`、`KO-INIT`、`ALIAS-DEF`、`ALIAS-FILE` |
| netlink_trace | `NL-FAMILY-DEF`、`NL-REGISTER`、`NL-OP`、`NL-OPS-DEF`、`NL-USER-RESOLVE`、`NL-USER-REF`、`NL-PROTO` |
| media_topo | `V4L2-OPS`、`V4L2-INIT`、`MEDIA-PADS`、`V4L2-ASYNC-REG`、`MEDIA-LINK`、`V4L2-PROBE`、`V4L2-COMPAT`、`V4L2-DT-NODE`、`V4L2-DT-PORT`、`V4L2-DT-ENDPOINT`、`ENTITY-REF`、`ENTITY-NAME` |

### 降级策略

```
domain_find 未识别       → 提示用显式参数或切换到 xlang_find
子脚本未命中             → rg 全文搜索
rg 未命中                → 检查拼写、确认索引覆盖该目录
selinux_trace 找不到 .te → 检查 system/sepolicy/ 和 device/*/sepolicy/ 是否在搜索范围内
kconfig_trace 未命中     → 确认 defconfig 路径正确、CONFIG 符号拼写正确
build_trace 未命中       → 可能在 prebuilt 目录或 vendor 闭源模块里
```

### 局限

- **DT**：仅基于文本匹配 `compatible` 字符串；`of_match_table` 如果用变量间接引用可能漏；DT binding 文档搜索仅限 `Documentation/devicetree/bindings/` 目录。
- **DTBO overlay**：仅 Layer 1 文本搜索，不反编译 dtbo.img；`target = <&phandle>` 只做文本匹配，不解析 phandle 实际值。
- **sysfs**：`DEVICE_ATTR` 四参数形式的非标准回调名可识别，但 `__ATTR` 等简写宏的回调提取靠启发式；`proc_ops` 仅在同文件里找。
- **Binder**：不追 Binder 运行时路由和 ServiceManager 实际注册时机；VINTF 搜索基于文件名 glob（`manifest*.xml`），非标准命名可能漏。
- **SELinux**：仅做 `.te` 文件文本搜索，不展开 `m4` 宏（`hal_server_domain` 等）；`neverallow` 冲突检测需 `sepolicy-analyze`，本脚本不做。
- **subsys_trace**：clock/regulator/GPIO 等 consumer 搜索基于 API 函数名模式匹配；如果 driver 用了自定义封装函数可能漏。
- **prop_trace**：不追 property 在进程间传递的运行时链路；仅搜索常见 API（`SystemProperties`/`__system_property_get`/`android::base::GetProperty`）。
- **build_trace**：`Android.bp` 模块类型到安装路径的推断是启发式的；条件编译（`product_variables`）不解析。VNDK 检查仅看 `vendor_available` / `vndk` 字段，不做完整的 linker namespace 分析。
- **initrc_trace**：trigger block 解析基于缩进和下一个 `on`/`service` 关键字，不做完整的 init 语法解析。
- **kconfig_trace**：不展开 `if`/`endif` 块的 Kconfig 依赖树；`IS_ENABLED` 搜索需要完整的 `CONFIG_` 前缀。
- **firmware_trace**：`request_firmware` 如果 firmware name 通过变量传入（非字面量字符串）会漏；`MODULE_DEVICE_TABLE` 只按文件名匹配模块，可能误关联。
- **netlink_trace**：scan 模式会捞到大量 `.name = "xxx"` 非 netlink family 的误命中；建议用 `--family` 精确追踪。
- **media_topo**：仅静态分析，不追运行时 media-ctl 配置的 link；subdev 匹配基于文件名，跨文件的 subdev 注册可能漏。

### 常见问题

**Q: dt_bind 没找到 driver，但 DTS 里确实有这个 compatible？**
三种可能：(1) driver 在 vendor 闭源 blob 里（无源码）；(2) `of_match_table` 用了宏/变量间接引用；(3) driver 是通过 `platform_device_register_*` 手动注册而非 DT 匹配。

**Q: sysfs_attr 找不到回调函数？**
(1) 属性可能用 `__ATTR` 简写宏而非 `DEVICE_ATTR`；(2) 回调命名不符合 `<attr>_show`/`<attr>_store` 约定——用 `--callback <func_name>` 反查。

**Q: binder_svc 找不到 service 注册点？**
(1) 可能用 `lazyRegistrar` 或 `AServiceManager_*` NDK API；(2) Java 侧可能通过 `publishBinderService` 或 `SystemServiceManager`；这些非标准模式可用 rg 全文搜。

**Q: selinux_trace --avc 建议的 allow 规则加了但还是被拒？**
可能触发了 `neverallow` 规则。检查 `system/sepolicy/` 下的 neverallow 语句。也可能需要自定义 type 而非直接给已有 type 加权限。

**Q: subsys_trace --clock 找不到 provider？**
(1) provider 可能在 SoC vendor 目录的 clock driver 里，确认搜索范围覆盖 `drivers/clk/vendor/`；(2) CLK_OF_DECLARE 宏可能嵌套在 `#ifdef` 里。

**Q: prop_trace 没找到某个 property 的写入方？**
(1) 可能在 native daemon 里通过 `property_set` 写入——搜 `property_set`；(2) 可能从 build 系统注入到 .prop 文件——搜 `PRODUCT_PROPERTY_OVERRIDES`。

**Q: build_trace 找不到模块定义？**
(1) 模块可能在 `prebuilts/` 目录里；(2) 模块名可能和 `Android.bp` 里的 `name` 不完全一致（如带 `lib` 前缀）。

**Q: initrc_trace --service 没找到启动 trigger？**
(1) service 可能标记了 `disabled`，由其他进程通过 `ctl.start` property 启动；(2) service 可能通过 `class_start` 批量启动而非单独 `start`。

**Q: kconfig_trace 显示的 #ifdef 太多？**
对于常见 CONFIG（如 `CONFIG_OF`），引用遍布内核。聚焦 `MAKEFILE-OBJ` tag 看编译控制，`KCONFIG-DEF` 看定义和依赖。

**Q: media_topo 只找到部分 subdev？**
(1) 完整的 media pipeline 需要 `media-ctl -p` 运行时输出；(2) 跨驱动的 async notifier 绑定可能不在 subdev driver 文件里——搜 `v4l2_async_nf_add_fwnode`。

<!-- END: androidbsp-domaintrace-setup -->
