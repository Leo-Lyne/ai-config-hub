---
name: androidbsp-domaintrace-setup
description: '在已配置好代码索引的 Android BSP 项目上，部署领域知识驱动的多步追踪工具链（DT / DTBO / sysfs-procfs-debugfs / Binder-VINTF / SELinux / clock-regulator-GPIO-IRQ-power-domain / Android Property / Build-VNDK / init.rc / Kconfig / Firmware-kmod / Netlink / V4L2），并把使用规则作为模板注入到工作区 AGENTS.md。与 codecross 互补：codecross 处理符号变形/编码导致 rg 搜不到的边界（JNI / syscall / ioctl），domaintrace 处理 rg 能搜到但需要领域知识串联多步的追踪。前置：已跑过 `androidbsp-codeindex-setup`。触发词：「compatible 对应哪个 driver」「sysfs 属性的回调」「service 注册在哪个进程」「VINTF manifest」「avc denied 怎么改」「SELinux 策略在哪」「DT property 谁在读」「/sys/class 的实现」「这个 clock/regulator/GPIO 谁在用」「ro.xxx property 谁读写」「libxxx.so 哪个模块编的」「CONFIG_XXX 影响啥」「init.rc trigger 链」「firmware 从哪加载」「nl80211 family」「V4L2 subdev 拓扑」「DTBO overlay 改了啥」「部署 alltrace」「部署 domaintrace」「/domaintrace setup」。'
command: /domaintrace
args:
  - name: setup
    description: '部署领域追踪脚本，并把使用规则从模板注入到项目 AGENTS.md'
    required: false
---

# androidbsp-domaintrace-setup

**职责单一**：在已配置好索引的 Android BSP 上，部署领域知识驱动的多步追踪脚本，并把使用规则注入项目 `AGENTS.md`。

### 与 codecross 的分工

| skill | 定位 | 为什么单独存在 |
|---|---|---|
| **codecross** | 跨边界：符号变形/编码，rg 搜不到对面 | JNI mangling、AIDL 生成代码、syscall 号编码、ioctl 命令号编码 |
| **domaintrace**（本 skill） | 领域追踪：rg 能搜到，但需要领域知识决定搜什么、串联哪些步骤 | DT、sysfs、Binder、SELinux、子系统资源、Property、Build、init.rc、Kconfig、Firmware、Netlink、V4L2 |

两者通过各自的统一入口互补：
- `xlang_find.py`（codecross）——符号变形类追踪
- `domain_find.py`（domaintrace）——领域知识类追踪

本 skill 覆盖的追踪维度：

| 维度 | 形式 | 典型场景 |
|---|---|---|
| 硬件描述 | DT compatible（DTS ↔ driver `of_device_id`） | 追 `compatible` 到匹配的 driver probe |
| 硬件描述 | DT property（`of_property_read_*` ↔ DTS 定义） | 追 property 在驱动和设备树中的使用 |
| 硬件描述 | DTBO overlay（`__overlay__` + `target-path`） | 追 overlay 改了 base DT 的哪些节点 |
| 内核导出 | sysfs/procfs/debugfs（节点名 ↔ 回调函数） | 追 sysfs 属性到 `show`/`store` 回调 |
| 进程/服务 | Binder service（注册 ↔ 进程 ↔ VINTF manifest） | 追 service 到 .rc 进程和 HAL 声明 |
| 安全策略 | SELinux（domain/type ↔ .te 策略 ↔ contexts） | 追 avc denied 到该改的 .te 文件 |
| 子系统资源 | clock / regulator / GPIO / IRQ / power-domain | provider ↔ consumer ↔ DT 三端配对 |
| Android 属性 | Property（Java/native 读写 ↔ .prop ↔ init.rc trigger） | property 全链路读写和 trigger 追踪 |
| Build 系统 | Android.bp/mk → 模块 → 安装路径；VNDK | 模块从定义到 image 再到可见性 |
| 初始化 | init .rc（trigger → action → service） | service 启动条件、property trigger 链 |
| 内核配置 | Kconfig ↔ defconfig ↔ #ifdef ↔ Makefile | CONFIG_XXX 完整影响链路 |
| 固件/模块 | `request_firmware` ↔ 文件系统；`MODULE_DEVICE_TABLE` ↔ 自动加载 | firmware 打包路径，kmod 自动加载 |
| 网络 | Netlink family（kernel 注册 ↔ userspace 使用） | genl_register_family 双向追踪 |
| 多媒体 | V4L2 / Media Controller 静态拓扑 | subdev 注册 + 静态 pad link + DT port |

约定：`$BSP_ROOT` 默认为当前工作目录；`$SKILL_DIR` 指本 skill 所在目录（`skills/androidbsp-domaintrace-setup/`）。

---

## 前置要求

**必须先跑过 `androidbsp-codeindex-setup`。** 本 skill 产出的脚本内部依赖 `rg`、`compile_commands.json`、`GTAGS`，以及公共库 `_bsp_common.py`——全部由索引 skill 负责部署与验证。

**判据**：工件齐全 + 公共库版本兼容。

```bash
cd $BSP_ROOT
[ -f .codenav/scripts/_bsp_common.py ] \
  && [ -f compile_commands.json ] \
  && [ -f GTAGS ] \
  || { cat <<'EOF'
❌ 前置要求未满足：codeindex-setup 未完成部署。
缺少以下任一工件：.codenav/scripts/_bsp_common.py、compile_commands.json、GTAGS。
请先跑：
  /codeindex setup
EOF
  exit 1
}

# 验证公共库版本兼容
python3 -c "import sys; sys.path.insert(0,'.codenav/scripts'); \
  import _bsp_common as c; c.require_version('1.0.0')" \
  || { echo "FAIL: _bsp_common 版本过旧，请重跑 /codeindex setup 升级"; exit 1; }
```

不满足就停。不要自作主张去装工具——那是索引 skill 的职责。

---

## 部署步骤（`/domaintrace setup`）

### 1. 部署脚本

```bash
cd $BSP_ROOT
mkdir -p .codenav/scripts

# 原始 4 个追踪脚本
cp $SKILL_DIR/scripts/dt_bind.py          .codenav/scripts/
cp $SKILL_DIR/scripts/sysfs_attr.py       .codenav/scripts/
cp $SKILL_DIR/scripts/binder_svc.py       .codenav/scripts/
cp $SKILL_DIR/scripts/selinux_trace.py    .codenav/scripts/

# 新增 8 个追踪脚本
cp $SKILL_DIR/scripts/subsys_trace.py     .codenav/scripts/   # clock/regulator/GPIO/IRQ/power-domain
cp $SKILL_DIR/scripts/prop_trace.py       .codenav/scripts/   # Android Property 系统
cp $SKILL_DIR/scripts/build_trace.py      .codenav/scripts/   # Android.bp/mk + VNDK
cp $SKILL_DIR/scripts/initrc_trace.py     .codenav/scripts/   # init.rc trigger 链 + USB gadget
cp $SKILL_DIR/scripts/kconfig_trace.py    .codenav/scripts/   # Kconfig ↔ 代码
cp $SKILL_DIR/scripts/firmware_trace.py   .codenav/scripts/   # firmware 加载 + kmod 自动加载
cp $SKILL_DIR/scripts/netlink_trace.py    .codenav/scripts/   # Netlink family
cp $SKILL_DIR/scripts/media_topo.py       .codenav/scripts/   # V4L2 静态拓扑

# 新增 2 个追踪脚本（bootcfg / APEX）
cp $SKILL_DIR/scripts/bootcfg_trace.py    .codenav/scripts/   # bootconfig / cmdline 参数 ↔ 读取点
cp $SKILL_DIR/scripts/apex_locate.py      .codenav/scripts/   # APEX 模块定位与 manifest 追踪

# 统一入口
cp $SKILL_DIR/scripts/domain_find.py      .codenav/scripts/

chmod +x .codenav/scripts/*.py
```

### 2. 注入 AGENTS.md 使用规则（标记块幂等注入）

调用 codeindex 部署的 `_inject_block.sh`（基于模板首行 `v=N` 版本标记做幂等/升级判定）：

```bash
cd $BSP_ROOT
.codenav/scripts/_inject_block.sh androidbsp-domaintrace-setup \
  $SKILL_DIR/assets/AGENTS.md.domaintrace.template AGENTS.md
```

同版本已注入 → 跳过；模板升 `v=N` → 替换 BEGIN…END 之间的块；未注入过 → 追加。

### 3. 冒烟验证

```bash
cd $BSP_ROOT

# 脚本可执行
for s in domain_find dt_bind sysfs_attr binder_svc selinux_trace \
         subsys_trace prop_trace build_trace initrc_trace \
         kconfig_trace firmware_trace netlink_trace media_topo \
         bootcfg_trace apex_locate; do
  python3 .codenav/scripts/$s.py --help >/dev/null && echo "$s OK" || echo "$s FAIL"
done

# AGENTS.md 模板段落已合入且仅一份
[ "$(grep -c 'BEGIN: androidbsp-domaintrace-setup' AGENTS.md)" = "1" ] && echo "AGENTS.md 注入 OK"
```

全过 → 部署完成。使用规则已在 AGENTS.md 里，AI 日常工作时自动读取。

---

## 什么时候重跑

| 场景 | 动作 |
|---|---|
| 本 skill 的脚本升级 | 重跑 `setup`（`.codenav/scripts/` 里 15 个 .py 被覆盖；AGENTS.md 段落因幂等标记会跳过，模板 `v=N` 升版才替换） |
| AGENTS.md 模板有更新，想强制重注入 | 手工删除 `<!-- BEGIN: … -->` 到 `<!-- END: … -->` 之间的内容，再跑 `setup` |
| `androidbsp-codeindex-setup` 被完全重装（AGENTS.md 被覆盖） | 重跑本 skill `setup`，重新注入 domaintrace 段落 |

---

## 目录速查

```
skills/androidbsp-domaintrace-setup/
├── SKILL.md                              # 本文件（部署流程）
├── assets/
│   └── AGENTS.md.domaintrace.template       # 运行时使用规则（给工作区 AI 日常读的单一事实源）
├── scripts/
│   ├── domain_find.py                    # 统一入口，按符号形态自动派发
│   │
│   │ ── 原始 4 个（基础领域追踪） ──
│   ├── dt_bind.py                        # DT：compatible ↔ driver + DTBO overlay（Layer 1）
│   ├── sysfs_attr.py                     # sysfs/procfs/debugfs：节点名 ↔ show/store 回调
│   ├── binder_svc.py                     # Binder：service 注册 ↔ 进程 ↔ VINTF manifest
│   ├── selinux_trace.py                  # SELinux：domain/type ↔ .te 策略 ↔ contexts
│   │
│   │ ── 新增 8 个（扩展追踪能力） ──
│   ├── subsys_trace.py                   # 子系统资源：clock/regulator/GPIO/IRQ/power-domain
│   ├── prop_trace.py                     # Android Property：Java/native/.prop/init.rc 全链路
│   ├── build_trace.py                    # Build：Android.bp/mk → 模块 → 安装路径 + VNDK
│   ├── initrc_trace.py                   # init.rc：trigger → action → service + USB gadget
│   ├── kconfig_trace.py                  # Kconfig：defconfig ↔ 定义 ↔ #ifdef ↔ Makefile
│   ├── firmware_trace.py                 # Firmware：request_firmware + 打包；kmod 自动加载
│   ├── netlink_trace.py                  # Netlink：genl_register_family ↔ userspace 使用
│   ├── media_topo.py                     # V4L2：subdev 静态注册 + pad link + DT port
│   │
│   │ ── 新增 2 个（启动/打包维度） ──
│   ├── bootcfg_trace.py                  # bootconfig / cmdline 参数 ↔ 读取点
│   └── apex_locate.py                    # APEX 模块定位与 manifest 追踪
└── evals/evals.json                      # 本 skill 的测试用例
```

> 脚本的**使用规则**统一由 `assets/AGENTS.md.domaintrace.template` 负责，**不在 SKILL.md 里重复**。
> SKILL.md 只管 "怎么把环境部署到位"，一次性；AGENTS.md 模板管 "日常怎么用"，长期生效。

---

## 姐妹 skill

- `/code-cross setup` — 符号编码跨边界（JNI / AIDL / HIDL / syscall / ioctl）
- `/codenav setup-all` — 一步到位完整部署

## 未来：runtime-trace skill 接入

本 skill 部署的脚本默认向 `.codenav/events.jsonl` 追加 event。未来 runtime-trace
skill 会向同一文件追加 `runtime-ftrace` / `runtime-bpftrace` / `runtime-logcat` /
`runtime-dmesg` 等 source 的 event，AI agent 读取时按 schema `androidbsp.event/v1`
统一合并。本 skill 输出**已经满足这一契约**，无需改动。
