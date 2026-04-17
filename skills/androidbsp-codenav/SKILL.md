---
name: androidbsp-codenav
description: 'Android BSP 代码导航全生命周期管理：编排 codeindex / codecross / domaintrace 三个 setup skill 的首次部署、增量更新与状态检查。当用户说「全套部署 codenav」「一次性配置 BSP 代码导航」「/codenav setup-all」「BSP code nav setup」「更新 codenav」「codenav 状态」「索引过期了」「lunch target 换了要不要重建」「AGENTS.md 模板有更新吗」时使用。'
---

# androidbsp-codenav

**职责**：Android BSP 代码导航的全生命周期协调者——首次部署、增量更新、状态诊断。

本 skill **不**做实际部署工作——所有真实工作由三个 setup skill 执行：

| 组件 | Skill | 职责 |
|---|---|---|
| codeindex | `androidbsp-codeindex-setup` | `.active_files.idx` + `compile_commands.json` + `gtags` + `.clangd` + AGENTS.md 检索规则 |
| codecross | `androidbsp-codecross-setup` | JNI / AIDL / HIDL / syscall / ioctl 跨边界追踪脚本 + AGENTS.md 规则 |
| domaintrace | `androidbsp-domaintrace-setup` | DT / sysfs / Binder / SELinux / 子系统等 14+ 领域追踪脚本 + AGENTS.md 规则 |

---

## /codenav setup-all

全套初始化。依次以"前一步成功才进下一步"触发：

1. **androidbsp-codeindex-setup** 的全部 Phase
2. 通过 Phase 4 冒烟后 → **androidbsp-codecross-setup** 的 setup 流程
3. 通过冒烟后 → **androidbsp-domaintrace-setup** 的 setup 流程

任一步失败立即停止，输出失败原因。完成后输出汇总：

```
✅ codenav 部署完成
   - codeindex: <gtags 行数> / compdb <entries>
   - codecross: 5 个跨边界脚本就位
   - domaintrace: 14 个领域脚本就位
   - AGENTS.md: 3 段标记块齐全
```

---

## /codenav update

增量更新。检测哪些组件过期，**只重跑需要刷新的部分**。

### 检测逻辑

按下表逐项检查，有任一项过期则标记该组件需要更新：

#### codeindex 过期检测

| 检查项 | 检测方式 | 含义 |
|---|---|---|
| ninja 构建图比 `.active_files.idx` 新 | `[ out/combined-*.ninja -nt .codenav/active_files.idx ]` | 编译后源文件集合可能变了 |
| `_bsp_common.py` 版本落后 | 对比 skill 自带版本 vs `.codenav/scripts/` 部署版本 | 公共库有更新 |
| gtags DB 不存在或比 `.active_files.idx` 旧 | `[ .codenav/active_files.idx -nt GTAGS ]` | 索引需要重建 |

若 codeindex 过期：重跑 `androidbsp-codeindex-setup` 的 Phase 2 + 3（不需要重装工具）。

#### codecross 过期检测

| 检查项 | 检测方式 |
|---|---|
| 脚本版本落后 | 对比 skill 自带 `scripts/` 里每个 `.py` 的内容 hash 与 `.codenav/scripts/` 已部署版本 |
| AGENTS.md 缺少 codecross 标记块 | `grep -q '<!-- codecross-begin -->' AGENTS.md` |

若过期：重跑 `androidbsp-codecross-setup` 的 setup 流程。

#### domaintrace 过期检测

| 检查项 | 检测方式 |
|---|---|
| 脚本版本落后 | 同上，hash 对比 |
| AGENTS.md 缺少 domaintrace 标记块 | `grep -q '<!-- domaintrace-begin -->' AGENTS.md` |

若过期：重跑 `androidbsp-domaintrace-setup` 的 setup 流程。

### 执行流程

```
1. 运行上述检测
2. 汇总哪些组件需要更新
3. 告知用户检测结果，让用户确认后再执行
4. 按 codeindex → codecross → domaintrace 顺序重跑过期组件
5. 输出更新汇总
```

如果全部组件都是最新的，报告"全部组件已是最新"并退出。

### 常见 update 场景

| 用户场景 | 触发的更新 |
|---|---|
| `repo sync` 拉了新代码后重新编译 | codeindex（idx + gtags + compdb） |
| 切换 lunch target | codeindex（全部重建） |
| ai-config-hub 里的 skill 更新了 | codecross / domaintrace（脚本 + AGENTS.md 模板） |
| `_bsp_common.py` 升级 | codeindex 负责部署新版本 |
| AGENTS.md 被误删或部分缺失 | 缺失的组件重新注入 |

---

## /codenav status

报告当前 codenav 部署状态，不做任何修改。

### 检查项

```bash
cd $BSP_ROOT

echo "=== codeindex ==="
# active_files.idx
[ -f .codenav/active_files.idx ] \
  && echo "✓ active_files.idx — $(wc -l < .codenav/active_files.idx) files, $(stat -c '%y' .codenav/active_files.idx | cut -d. -f1)" \
  || echo "✗ active_files.idx missing"

# compile_commands.json
[ -f compile_commands.json ] \
  && echo "✓ compile_commands.json — $(python3 -c "import json; print(len(json.load(open('compile_commands.json'))),'entries')")" \
  || echo "✗ compile_commands.json missing"

# gtags
[ -f GTAGS ] \
  && echo "✓ GTAGS — $(stat -c '%y' GTAGS | cut -d. -f1)" \
  || echo "✗ GTAGS missing"

# _bsp_common.py version
[ -f .codenav/scripts/_bsp_common.py ] \
  && echo "✓ _bsp_common.py — v$(python3 -c "import sys; sys.path.insert(0,'.codenav/scripts'); import _bsp_common as c; print(c.BSP_COMMON_VERSION)")" \
  || echo "✗ _bsp_common.py missing"

echo ""
echo "=== codecross ==="
# 检查 5 个核心脚本
for script in xlang_find.py jni_trace.py aidl_trace.py syscall_trace.py ioctl_trace.py; do
  [ -f ".codenav/scripts/$script" ] \
    && echo "✓ $script" \
    || echo "✗ $script missing"
done

echo ""
echo "=== domaintrace ==="
[ -f .codenav/scripts/domain_find.py ] \
  && echo "✓ domain_find.py" \
  || echo "✗ domain_find.py missing"

echo ""
echo "=== AGENTS.md ==="
[ -f AGENTS.md ] || { echo "✗ AGENTS.md missing"; exit 0; }
grep -q '<!-- codeindex-begin -->'   AGENTS.md && echo "✓ codeindex block"   || echo "✗ codeindex block missing"
grep -q '<!-- codecross-begin -->'   AGENTS.md && echo "✓ codecross block"   || echo "✗ codecross block missing"
grep -q '<!-- domaintrace-begin -->' AGENTS.md && echo "✓ domaintrace block" || echo "✗ domaintrace block missing"
```

输出格式为三段式（codeindex / codecross / domaintrace），每项 ✓ 或 ✗，附带关键数值（文件数、条目数、版本号、时间戳）。

如果有 ✗ 项，建议用户执行 `/codenav update` 修复。

---

## 执行模式

AI agent 在收到 codenav 子命令时应：

1. 判断当前目录是否为 Android BSP（`build/envsetup.sh` + `device/` 存在）
2. 根据子命令路由：
   - `setup-all` → 依次调用三个 setup skill
   - `update` → 先运行检测，展示结果，用户确认后选择性重跑
   - `status` → 只读检查，不做修改
3. 各 setup skill 仍可独立调用（如 `/codeindex setup`），不必经由本 skill

---

## 目录速查

```
skills/androidbsp-codenav/
├── SKILL.md          # 本文件（编排 + 更新 + 状态）
└── evals/evals.json  # 测试用例
```

无 `scripts/` 或 `assets/`——本 skill 不部署任何文件，只调度别的 skill。
