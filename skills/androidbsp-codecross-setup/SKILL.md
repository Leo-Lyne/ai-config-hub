---
name: androidbsp-codecross-setup
description: '在已配置好代码索引的 Android BSP 项目上，部署跨边界调用链追踪工具链（JNI / AIDL / HIDL / syscall / ioctl），并把使用规则作为模板注入到工作区 AGENTS.md，让 AI agent 在日常开发中自动调用，追踪 gtags / rg 无法跨越的边界。前置：已跑过 `androidbsp-codeindex-setup` 并把索引使用规则注入 AGENTS.md。触发词：「追一下这个 JNI」「ICameraProvider 的实现链路」「AIDL / HIDL 接口追踪」「这个 syscall 在 kernel 哪里」「ioctl 命令号对应哪个宏」「binder_ioctl 的 case 列表」「/dev/binder 的驱动在哪」「native 方法对应的 C 实现」「跨语言 / 跨特权 / 跨边界追踪」「部署 code-cross」「/code-cross setup」。'
command: /code-cross
args:
  - name: setup
    description: '部署跨边界追踪脚本，并把使用规则从模板注入到项目 AGENTS.md'
    required: false
---

# androidbsp-codecross-setup

**职责单一**：在已配置好索引的 Android BSP 上，部署跨边界（JNI / AIDL / HIDL / syscall / ioctl）追踪脚本，并把使用规则注入项目 `AGENTS.md`。

本 skill **不**管普通符号/引用检索、不建 gtags、不生成 compile_commands.json——那是 `androidbsp-codeindex-setup` 的事。本 skill 只处理 gtags / rg 力有不逮的跨边界：

| 边界 | 形式 | 典型场景 |
|---|---|---|
| 跨语言 | JNI（C ↔ Java/Kotlin） | 追 `native` / `external fun` 的 C 实现 |
| 跨语言 | AIDL（`IFoo.aidl` ↔ Bn/Bp ↔ impl） | 追接口到服务端实现和客户端调用 |
| 跨语言 | HIDL（`IFoo.hal` ↔ BnHw/BpHw/Bs ↔ impl） | 老版本 BSP 仍在用 |
| 跨特权 | syscall（userspace → `__NR_*` → kernel） | 追系统调用到内核入口 |
| 跨特权 | ioctl（用户宏 ↔ 驱动 handler） | 追设备命令到 driver case |

> **领域知识驱动的多步追踪**（DT compatible、sysfs 回调、Binder service、SELinux 策略）
> 请用 `androidbsp-domaintrace-setup`——它们 rg 能搜到，但需要领域知识串联多步。

约定：`$BSP_ROOT` 默认为当前工作目录；`$SKILL_DIR` 指本 skill 所在目录（`skills/androidbsp-codecross-setup/`）。

---

## 前置要求

**必须先跑过 `androidbsp-codeindex-setup` 并把索引使用规则注入 AGENTS.md。** 本 skill 产出的脚本内部依赖 `global`（gtags）、`rg`、`compile_commands.json`——这些工件由索引 skill 负责部署与验证，本 skill 不重复做。

**唯一判据**：项目根 `AGENTS.md` 存在且包含 `androidbsp-codeindex-setup` 字样（索引 skill 的模板注入标记）。

```bash
cd $BSP_ROOT
grep -q "androidbsp-codeindex-setup" AGENTS.md 2>/dev/null || {
  cat <<'EOF'
❌ 前置要求未满足：未检测到 androidbsp-codeindex-setup 的部署痕迹。

本 skill 依赖索引 skill 产出的 gtags / compile_commands.json / rg 等环境，
以及 AGENTS.md 中已注入的索引使用规则。请先跑：

  /codeindex setup

确认项目根 AGENTS.md 里存在 "androidbsp-codeindex-setup" 段落后再回来跑 setup。
EOF
  exit 1
}
```

不满足就停。不要自作主张去建 gtags / compdb / 装工具——那是索引 skill 的职责，重复做会破坏它的单一事实源。

---

## 部署步骤（`/code-cross setup`）

### 1. 部署脚本

```bash
cd $BSP_ROOT
mkdir -p scripts
cp $SKILL_DIR/scripts/jni_bridge.py     scripts/
cp $SKILL_DIR/scripts/aidl_bridge.py    scripts/
cp $SKILL_DIR/scripts/syscall_trace.py  scripts/
cp $SKILL_DIR/scripts/ioctl_trace.py    scripts/
cp $SKILL_DIR/scripts/xlang_find.py     scripts/
chmod +x scripts/*.py
```

### 2. 注入 AGENTS.md 使用规则（模板追加）

把 `assets/AGENTS.md.codecross.template` 追加到项目根 `AGENTS.md` 末尾。模板首尾带
`<!-- BEGIN/END: androidbsp-codecross-setup -->` 标记，用于幂等——已注入就跳过，避免重复。

```bash
cd $BSP_ROOT
MARKER="BEGIN: androidbsp-codecross-setup"
if grep -qF "$MARKER" AGENTS.md; then
  echo "AGENTS.md 已注入过 codecross 段落，跳过。要强制重注入，手工删除 BEGIN…END 之间的块再重跑。"
else
  cat $SKILL_DIR/assets/AGENTS.md.codecross.template >> AGENTS.md
  echo "codecross 段落已注入 AGENTS.md"
fi
```

Claude Code / Cursor / Codex 不需要再配——它们已由 `androidbsp-codeindex-setup` 接入同一份
`AGENTS.md`，本 skill 的内容顺带生效。

### 3. 冒烟验证

```bash
cd $BSP_ROOT

# 脚本可执行
python3 scripts/xlang_find.py --help >/dev/null && echo "xlang_find OK"

# 真实查询能跑通（能看到 USER-WRAPPER 或 KERNEL-ENTRY 任一行即通过）
python3 scripts/syscall_trace.py openat 2>/dev/null | head -1 | grep -qE "(USER-WRAPPER|KERNEL-ENTRY)" \
  && echo "syscall_trace OK" || echo "WARN: syscall_trace 未命中 openat，检查 compile_commands.json 是否覆盖 kernel"

# AGENTS.md 模板段落已合入且仅一份
[ "$(grep -c 'BEGIN: androidbsp-codecross-setup' AGENTS.md)" = "1" ] && echo "AGENTS.md 注入 OK"
```

三项全过 → 部署完成，退出。不要再解释使用方法——那些已经写在 AGENTS.md 里了，未来每次工作时
AI 会自己读。

---

## 什么时候重跑

| 场景 | 动作 |
|---|---|
| 本 skill 的脚本升级 | 重跑 `setup`（`scripts/` 里 5 个 .py 被覆盖；AGENTS.md 段落因幂等标记会跳过） |
| AGENTS.md 模板有更新，想强制重注入 | 手工删除 `<!-- BEGIN: … -->` 到 `<!-- END: … -->` 之间的内容，再跑 `setup` |
| 切换 lunch target / 重编 | **与本 skill 无关**——脚本不缓存索引，跨边界规则也不依赖具体 target。只需按索引 skill 的指引重建 gtags / compdb |
| `androidbsp-codeindex-setup` 被完全重装（AGENTS.md 被覆盖） | 重跑本 skill `setup`，重新注入 codecross 段落 |

---

## 目录速查

```
skills/androidbsp-codecross-setup/
├── SKILL.md                              # 本文件（部署流程）
├── assets/
│   └── AGENTS.md.codecross.template      # 运行时使用规则（给工作区 AI 日常读的单一事实源）
├── scripts/
│   ├── xlang_find.py                     # 统一入口，按符号形态自动派发
│   ├── jni_bridge.py                     # JNI：C ↔ Java/Kotlin
│   ├── aidl_bridge.py                    # AIDL / HIDL：接口 ↔ 生成代码 ↔ 实现
│   ├── syscall_trace.py                  # syscall：userspace → __NR_* → kernel 入口
│   └── ioctl_trace.py                    # ioctl：宏 ↔ 命令号 ↔ driver handler
└── evals/evals.json                      # 本 skill 的测试用例
```

> 脚本的**使用规则**（什么时候调哪个、输出格式、局限、降级策略）统一由
> `assets/AGENTS.md.codecross.template` 负责，**不在 SKILL.md 里重复**。
> SKILL.md 只管 "怎么把环境部署到位"，一次性；AGENTS.md 模板管 "日常怎么用"，长期生效。
