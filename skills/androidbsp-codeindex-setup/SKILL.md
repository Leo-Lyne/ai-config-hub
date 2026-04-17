---
name: androidbsp-codeindex-setup
description: 'Android BSP 代码索引环境一键配置：过滤未编译模块生成 `.active_files.idx`、建立 `compile_commands.json` / `gtags` / `.clangd` 索引、冒烟验证索引可用、把运行时检索规则模板合入项目。当用户执行 `/codeindex setup <lunch 命令>`，或说「配置代码导航」「重建索引」「更新 compdb」「生成 active filter」时使用本 skill。'
command: /codeindex
args:
  - name: setup
    description: '配置代码索引环境，接收编译命令作为参数（如 `source build/envsetup.sh && lunch rk3568_r-userdebug`）'
    required: false
---

# androidbsp-codeindex-setup

本 skill **只做三件事**：

1. **过滤未编译模块** → 生成 `.active_files.idx`
2. **建立索引** → `compile_commands.json` + `gtags` + `.clangd`（gtags 以 `.active_files.idx` 为输入）
3. **验证索引可用** → 冒烟测试

完成后把 `assets/AGENTS.md.template` 合入项目根作为运行时检索规则入口。

约定：`$BSP_ROOT` 默认为当前工作目录；`$SKILL_DIR` 指本 skill 所在目录（`skills/androidbsp-codeindex-setup/`）。

---

## 前置要求（Requirements）

**以下三条全部满足才能运行本 skill。任一条不满足就立即终止，输出失败提示——不要自动触发编译、不要跳过检查。**

1. **当前项目是 Android BSP**
   判据：根目录同时存在 `build/envsetup.sh` 和 `device/`。
2. **已完成一次全量编译**
   判据：存在 `out/combined-*.ninja` 或 `out/soong/build.ninja`（ninja 构建图）。
3. **有编译产物**
   判据：`out/target/product/<name>/` 非空（证明跑到过 make target）。

### 检查脚本

```bash
cd $BSP_ROOT

# 1) 是 Android BSP
[ -f build/envsetup.sh ] && [ -d device ] || { echo "FAIL: 不是 Android BSP（缺 build/envsetup.sh 或 device/）"; exit 1; }

# 2) 有编译输出
[ -d out ] && [ -n "$(ls -A out 2>/dev/null)" ] || { echo "FAIL: out/ 为空，未编译"; exit 1; }

# 3) 有 ninja 构建图
NINJA_FILE=$(ls out/combined-*.ninja 2>/dev/null | head -1)
[ -z "$NINJA_FILE" ] && [ -f out/soong/build.ninja ] && NINJA_FILE="out/soong/build.ninja"
[ -n "$NINJA_FILE" ] || { echo "FAIL: 未找到 ninja 构建图"; exit 1; }

# 4) 有 target product 产物
ls -d out/target/product/*/ >/dev/null 2>&1 || { echo "FAIL: out/target/product/*/ 缺失"; exit 1; }

echo "OK: NINJA_FILE=$NINJA_FILE"
```

### 失败响应模板

> ❌ 本 skill 的前置要求未满足：`<具体失败项>`。
>
> 本 skill 只在**已完整编译过**的 Android BSP 上运行，依赖 ninja 构建图与 target 产物生成索引。
>
> 请先完成一次全量编译再重试：
> ```
> source build/envsetup.sh && lunch <your-target>
> make -j$(nproc)        # 或项目自带的 ./build.sh
> ```

---

## Phase 1 — 工具安装

统一交给 Python 脚本处理，**不要手敲 apt**。脚本自动判断在线/离线：
在线走 `apt-get install`、顺带检查已安装工具是否有更新；
离线走 `deps/packages/*.deb`。

```bash
python3 $SKILL_DIR/deps/install_tools.py
```

常用选项：`--check-only`（只看状态）、`--offline`（强制离线）、`--online`（强制在线）。
离线场景的 `.deb` 由同目录 `fetch_deps.py` 在联网机器上预先抓取（详见 `deps/README.md`）。

脚本成功返回即保证 `rg / fd / gtags / ctags / clangd / fzf` 六个命令在 PATH 上可用。

---

## Phase 2 — 过滤未编译模块 → `.codenav/active_files.idx`

把公共库 + 本 skill 的脚本部署到 `$BSP_ROOT/.codenav/`，然后生成索引。
`.codenav/active_files.idx` 是后面 gtags 和 `arg` 的共同输入。

```bash
cd $BSP_ROOT
mkdir -p .codenav/scripts

# 部署公共库（仅当本地版本旧于 skill 自带版本）
TARGET=.codenav/scripts/_bsp_common.py
SRC=$SKILL_DIR/scripts/_bsp_common.py
if [ -f "$TARGET" ]; then
  CUR=$(python3 -c "import sys; sys.path.insert(0,'.codenav/scripts'); \
                    import _bsp_common as c; print(c.BSP_COMMON_VERSION)")
  NEW=$(python3 -c "import sys; sys.path.insert(0,'$SKILL_DIR/scripts'); \
                    import _bsp_common as c; print(c.BSP_COMMON_VERSION)")
  python3 -c "from packaging.version import Version as V; \
              import sys; sys.exit(0 if V('$CUR') < V('$NEW') else 1)" \
    && cp $SRC $TARGET && echo "INFO: _bsp_common.py upgraded $CUR → $NEW" \
    || echo "INFO: _bsp_common.py is $CUR (>= $NEW), skip"
else
  cp $SRC $TARGET
  echo "INFO: _bsp_common.py installed"
fi

# 部署本 skill 的 user-facing 脚本
cp $SKILL_DIR/scripts/bsp_filter_gen.py .codenav/scripts/
cp $SKILL_DIR/scripts/arg.sh            .codenav/scripts/
cp $SKILL_DIR/scripts/idx_diff.py       .codenav/scripts/   # 子系统差分（Task 17）
chmod +x .codenav/scripts/*.py .codenav/scripts/*.sh

# 生成 active_files.idx（脚本内部默认写到 .codenav/，并把旧版本备份为 .idx.prev）
python3 .codenav/scripts/bsp_filter_gen.py \
  -b "<用户原始编译命令，例如：source build/envsetup.sh && lunch rk3568_r-userdebug>" \
  --root $BSP_ROOT

wc -l .codenav/active_files.idx   # 典型 3 万 ~ 10 万行
```

脚本自动适配 Rockchip / Qualcomm / MTK / 展锐等所有 AOSP 派生 BSP，按 lunch target 解析 vendor / kernel 目录 / DTS include 链。

---

## Phase 3 — 建立索引

### 3.1 `compile_commands.json`（clangd 基础）

优先调项目自带的 `gen_compdb.py`；没有则从 ninja 构建图手动提取。

```bash
cd $BSP_ROOT
if [ -x ./gen_compdb.py ]; then
  python3 gen_compdb.py
else
  NINJA_FILE=$(ls out/combined-*.ninja 2>/dev/null | head -1)
  [ -z "$NINJA_FILE" ] && NINJA_FILE="out/soong/build.ninja"
  NINJA_BIN="./prebuilts/build-tools/linux-x86/bin/ninja"
  [ -x "$NINJA_BIN" ] || NINJA_BIN=ninja
  CC_RULES=$(awk '/^rule /{print $2}' "$NINJA_FILE" | grep -iE 'cc|cxx|clang|gcc|compile')
  $NINJA_BIN -f "$NINJA_FILE" -t compdb $CC_RULES > compile_commands.json
fi
```

### 3.2 `gtags`（以 `.codenav/active_files.idx` 为输入）

用 `.codenav/active_files.idx` 过滤出源码文件喂给 gtags——索引体积和耗时都显著下降，且符号集与当前 target 对齐。
**必须** `GTAGSLABEL=new-ctags`，否则 Kotlin / Rust / Go 不入库。

```bash
cd $BSP_ROOT

# 只挑源码语言的文件（gtags/universal-ctags 后端能识别的集合）
grep -E '\.(c|h|cc|cpp|cxx|hpp|java|kt|S|s|asm)$' \
  .codenav/active_files.idx > .codenav/gtags.files
wc -l .codenav/gtags.files

# 后台建索引（大项目 20~60 分钟）
GTAGSLABEL=new-ctags gtags -v -f .codenav/gtags.files 2>&1 | tee gtags.log &
```

### 3.3 `.clangd`（过滤 Android 专有 flag）

```bash
[ -f $BSP_ROOT/.clangd ] || cp $SKILL_DIR/assets/clangd.template $BSP_ROOT/.clangd
```

已存在就不覆盖，保留用户自定义。

---

## Phase 4 — 验证

四个冒烟测试全过才算完成。任一失败，定位后修，不要跳过。

```bash
cd $BSP_ROOT

# 1. compdb 可解析
python3 -c "import json; d=json.load(open('compile_commands.json')); print(f'compdb OK: {len(d)} entries')"

# 2. gtags 库存在、查得到 C 符号
[ -f GTAGS ] && [ -f GRTAGS ] && [ -f GPATH ] || { echo "FAIL: gtags DB 缺失"; exit 1; }
global -d main >/dev/null && echo "gtags C OK" || echo "WARN: gtags 无 main 定义"

# 3. Kotlin 后端生效（仅当项目含 .kt 文件时）
if grep -q '\.kt$' .codenav/active_files.idx; then
  [ "$(printenv GTAGSLABEL)" = "new-ctags" ] || echo "WARN: GTAGSLABEL≠new-ctags"
fi

# 4. arg 命令能跑
bash .codenav/scripts/arg.sh --version >/dev/null && echo "arg OK"
```

失败时的处置：
- `gtags DB 缺失` → Phase 3.2 尚未完成，等后台任务跑完或查 `gtags.log`。
- `compdb 为空` → ninja 构建图选错、或 CC 规则名没匹配上，回看 Phase 3.1。
- `clangd` 启动后崩溃 → 对照实际 `compile_commands.json` 首行里的 flag，把 Android 专有 flag 加到 `.clangd` 的 `Remove`。

---

## Phase 5 — 合入 AI 检索规则模板

把 `AGENTS.md.template` 拷入项目根，并让 Claude / Cursor 各自的入口指向它。这里不生成内容，**只做搬运**——内容单一事实源在 `assets/AGENTS.md.template`。

```bash
cd $BSP_ROOT

# 主模板：AGENTS.md（opencode / Codex / antigravity 直接读）
cp $SKILL_DIR/assets/AGENTS.md.template AGENTS.md

# Claude Code：完整索引指引（hook 按需注入）
mkdir -p .claude/contexts
cp $SKILL_DIR/assets/codeindex_full.md .claude/contexts/codeindex.md

# Claude Code：一行 import
[ -f CLAUDE.md ] && grep -q '@AGENTS.md' CLAUDE.md || echo '@AGENTS.md' >> CLAUDE.md

# Cursor：指向同一份
mkdir -p .cursor/rules
cat > .cursor/rules/android-bsp.mdc <<'EOF'
---
alwaysApply: true
---
See @AGENTS.md for Android BSP code-search rules.
EOF
```

**完成。** 重启对应 AI 工具即生效。

---

## 什么时候重跑本 skill

| 场景 | 重跑范围 |
|---|---|
| 切换 lunch target | Phase 2 + 3 全跑（idx/compdb/gtags 都要重建） |
| 增量编译（源文件增删不大） | 通常无需重跑 |
| 新增 / 删除大量源文件 | Phase 2 + 3 |
| clangd 突然崩溃 | 只改 `.clangd`（把新出现的 flag 加到 `Remove`） |
| 工具版本过旧 | `python3 $SKILL_DIR/deps/install_tools.py`（在线模式会报告可升级项） |

---

## 目录速查

```
skills/androidbsp-codeindex-setup/
├── SKILL.md                         # 本文件（配置流程）
├── assets/
│   ├── AGENTS.md.template           # 运行时检索规则（Phase 5 合入项目）
│   └── clangd.template              # .clangd 默认值
├── deps/
│   ├── install_tools.py             # Phase 1 的唯一入口
│   ├── fetch_deps.py                # 预抓离线 .deb（联网机器运行一次）
│   ├── packages/                    # 离线 .deb 缓存
│   └── README.md
├── scripts/
│   ├── _bsp_common.py               # 公共库（部署到 $BSP_ROOT/.codenav/scripts/）
│   ├── _inject_block.sh             # AGENTS.md 标记块注入工具
│   ├── bsp_filter_gen.py            # Phase 2：生成 .codenav/active_files.idx
│   ├── idx_diff.py                  # idx 与 idx.prev 子系统差分
│   └── arg.sh                       # Active Ripgrep 入口
└── evals/evals.json                 # 本 skill 的测试用例
```
