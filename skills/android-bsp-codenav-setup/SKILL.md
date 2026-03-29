---
name: android-bsp-codenav-setup
description: '为 Android BSP 项目（RK/MTK/高通/展锐）配置代码导航环境。当用户需要：生成 compile_commands.json、重建 gtags/ctags 索引、启动 OpenGrok MCP、生成 AGENTS.md 指令文件、配置 Active Filter，或用户说"配置代码导航"、"重建索引"、"更新 compdb"时，使用此 skill。代码检索规则请使用 android-bsp-codesearch skill。'
---

# android-bsp-codenav-setup — Android BSP 项目代码导航环境搭建

> **注意**：本 skill 负责环境搭建（工具安装、索引构建、指令文件生成）。日常代码检索的工具选择和策略请参考 `android-bsp-codesearch` skill。

---

## 前提条件

使用此 skill 为android bsp项目配置代码导航工具前确认两个前提：
1. **已有完整 Android BSP 包**（含内核、HAL、vendor 等）
2. **已完整编译通过一次**（ninja 文件和构建产物存在）

本 skill 接收用户的编译命令作为参数（如 `source build/envsetup.sh && lunch rk3568_r-userdebug`），自动从中解析 product name、vendor、lunch target 等信息。`$BSP_ROOT` 默认为当前工作目录。

---

## Phase 1：工具安装与验证

### 一键检查所有工具

```bash
for tool in fzf fd rg gtags ctags clangd locate; do
  if command -v $tool &>/dev/null; then
    echo "✓ $tool: $(command -v $tool)"
  else
    echo "✗ $tool: 未安装"
  fi
done
```

### 安装缺失工具（Ubuntu/Debian）

```bash
# 基础搜索工具
sudo apt-get install -y ripgrep fd-find fzf universal-ctags

# GNU Global (gtags)
sudo apt-get install -y global

# clangd（建议 14+）
sudo apt-get install -y clangd

# locate（updatedb 更新数据库）
sudo apt-get install -y mlocate

# fd 在部分系统中二进制名为 fdfind，建议创建别名
which fdfind &>/dev/null && sudo ln -sf $(which fdfind) /usr/local/bin/fd
```

验证安装成功：
```bash
rg --version && fd --version && fzf --version && gtags --version && ctags --version && clangd --version
```

---

## Phase 2：索引构建

### 2.1 compile_commands.json（clangd 语义索引基础）

`compile_commands.json` 从 ninja 构建图提取，需要先完成编译。

**方法一：自动脚本（推荐，适用于已有 `gen_compdb.py` 的项目）**

```bash
cd $BSP_ROOT
python3 gen_compdb.py
```

脚本自动查找 `out/combined-*.ninja`，适用于所有 AOSP 派生 BSP。

**方法二：手动生成（通用，无需 gen_compdb.py）**

```bash
cd $BSP_ROOT

# 自动检测 ninja 文件（不要写死目标名）
NINJA_FILE=$(ls out/combined-*.ninja 2>/dev/null | head -1)
[ -z "$NINJA_FILE" ] && NINJA_FILE="out/soong/build.ninja"
echo "Using: $NINJA_FILE"

# 提取 CC/CXX 规则名
NINJA_BIN="./prebuilts/build-tools/linux-x86/bin/ninja"
CC_RULES=$(awk '/^rule /{print $2}' "$NINJA_FILE" | grep -iE 'cc|cxx|clang|gcc|compile')

# 生成
$NINJA_BIN -f "$NINJA_FILE" -t compdb $CC_RULES > compile_commands.json
du -sh compile_commands.json   # 正常为 500MB~2GB
```

**方法三：Soong 原生（部分平台）**

```bash
cd $BSP_ROOT
source build/envsetup.sh && lunch $LUNCH_TARGET
SOONG_GEN_COMPDB=1 SOONG_LINK_COMPDB_TO=$PWD \
    build/soong/soong_ui.bash --make-mode nothing
```

**验证**：
```bash
ls -lh $BSP_ROOT/compile_commands.json
python3 -c "import json; d=json.load(open('compile_commands.json')); print(f'OK: {len(d)} entries')"
```

---

### 2.2 gtags（GNU Global 符号交叉引用）

gtags 轻量快速，不依赖编译结果，适合终端中符号查找。

**生成文件列表（排除构建产物，大幅提速）：**

```bash
cd $BSP_ROOT

# 生成文件列表，排除 out/ .repo/ .git/ prebuilts/ 等
find . -type f \( -name "*.c" -o -name "*.h" -o -name "*.cpp" \
  -o -name "*.cc" -o -name "*.java" -o -name "*.kt" \
  -o -name "*.S" -o -name "*.s" \) \
  ! -path "./out/*" ! -path "./.repo/*" ! -path "./.git/*" \
  ! -path "./prebuilts/*" ! -path "*/node_modules/*" \
  > gtags.files

wc -l gtags.files   # 确认文件数量合理（典型 200k~600k）
```

**建立索引：**

```bash
gtags -v -f gtags.files 2>&1 | tee gtags.log &
# 后台运行，典型耗时 20~60 分钟，可用 tail -f gtags.log 跟踪进度
```

**验证：**

```bash
ls -lh $BSP_ROOT/GTAGS $BSP_ROOT/GRTAGS $BSP_ROOT/GPATH
global -d main   # 应返回 main 函数定义
```

---

### 2.3 ctags（快速标签导航）

```bash
cd $BSP_ROOT

# 生成 tags 文件（排除 out/）
ctags -R --exclude=out --exclude=.repo --exclude=.git \
  --languages=C,C++,Java,Kotlin \
  -f tags . &

# tags 文件典型大小 3~8GB，后台生成
```

**验证：**

```bash
ls -lh $BSP_ROOT/tags
readtags -t $BSP_ROOT/tags main | head -5
```

---

### 2.4 locate 数据库更新

```bash
sudo updatedb --prunepaths="/proc /sys /dev /run $BSP_ROOT/out $BSP_ROOT/.repo"
```

**验证：**

```bash
locate "$LUNCH_TARGET.mk" | head -5
```

---

## Phase 3：clangd 配置

clangd 需要 `compile_commands.json`，同时需要过滤 Android 专有编译 flags。

在 `$BSP_ROOT/.clangd` 创建配置（如不存在）：

```yaml
# .clangd
CompileFlags:
  Remove:
    - -mno-global-merge
    - -fdebug-prefix-map=*
    - --target=*
    - -Wa,*
    - -fno-ipa-sra
    - -march=armv8-2a*
  Add:
    - -Wno-everything
Index:
  Background: Build   # 后台异步建立符号索引
```

**说明**：`Remove` 列表过滤掉 Android 交叉编译专有 flags，防止 clangd crash；`Background: Build` 让 clangd 启动后自动异步建索引。

在支持 LSP 的编辑器（VS Code、Neovim 等）中打开项目，clangd 自动读取 `compile_commands.json`。

---

## Phase 4：OpenGrok MCP 服务

OpenGrok 提供 Web 全文+符号搜索，MCP 服务器让 AI 可以直接调用搜索 API。

### 4.1 启动 OpenGrok Docker 容器

```bash
cd $BSP_ROOT/opengrok
docker compose up -d

# 检查容器状态
docker compose ps
docker compose logs --tail=20

# 访问 Web UI（可选，用于人工验证）：http://localhost:8080
```

等待索引完成（首次启动会自动建立索引，可能需要数小时）：

```bash
# 跟踪索引进度
docker compose logs -f | grep -i "index"
```

### 4.2 MCP 服务器配置

项目根目录的 `.mcp.json` 已配置好 MCP 客户端集成：

```json
{
  "mcpServers": {
    "opengrok": {
      "command": "<BSP_ROOT>/opengrok/.venv/bin/python3",
      "args": ["<BSP_ROOT>/opengrok/opengrok_mcp.py"],
      "env": { "OPENGROK_URL": "http://localhost:8080/api/v1" }
    }
  }
}
```

如 `.mcp.json` 不存在，按上述模板创建（替换 `<BSP_ROOT>` 为绝对路径）。

Claude Code 在项目目录启动时会自动加载 `.mcp.json` 中的 MCP 服务器，无需手动启动。

### 4.3 验证 MCP 工具可用

在对话中直接调用：
```
mcp__opengrok__search_opengrok("drm_bridge_ops", "def")
```
返回文件路径和行号即表示正常工作。

---

## Phase 5：多工具指令文件生成

在 BSP 根目录创建指令文件，让各 AI 工具自动遵循代码检索规则。

**内容来源**：`android-bsp-codesearch` skill 中的检索规则，加上当前项目的环境信息。

| AI 工具 | 指令文件 | 说明 |
|---------|---------|------|
| Claude Code | `CLAUDE.md` | 通过 `@AGENTS.md` 导入 |
| opencode / Codex / antigravity | `AGENTS.md` | 直接读取 |
| Cursor | `.cursor/rules/android-bsp.mdc` | MDC 格式，alwaysApply |

**策略**：以 `AGENTS.md` 为单一事实来源，其他文件引用或同步它。

### 5.1 AGENTS.md

在 `$BSP_ROOT/AGENTS.md` 创建，内容应包含：
- `android-bsp-codesearch` skill 中的工具优先级表、降级策略、自动行为规则
- 当前项目的环境信息（BSP Root、compile_commands.json 路径、gtags 数据库位置、OpenGrok MCP 配置）

### 5.2 CLAUDE.md

在 `$BSP_ROOT/CLAUDE.md` 创建：

```markdown
@AGENTS.md
```

### 5.3 .cursor/rules/android-bsp.mdc

```bash
mkdir -p $BSP_ROOT/.cursor/rules
```

在 `$BSP_ROOT/.cursor/rules/android-bsp.mdc` 创建，frontmatter 设置 `alwaysApply: true`，正文内容与 AGENTS.md 一致。

**三个文件创建完成后，重新启动对应工具即可生效。**

---

## Phase 6：全栈 BSP 过滤器 (Active Filter)

解决 Android 项目文件过多、搜索噪音大的问题。通过 `bsp_filter_gen.py` 融合三类数据源生成当前 Target 的活跃文件索引：

| 数据源 | 覆盖范围 | 依赖条件 |
|--------|----------|----------|
| `compile_commands.json` | C/C++ 源码 | 已完成编译 |
| `module-info.json` + `installed-files.txt` | Java/Kotlin 模块源码 | 已完成编译 |
| Build command 解析 | DTS include 链、defconfig、设备树模板、device configs | 提供 `--build-cmd` |

脚本支持所有 AOSP 派生平台（Rockchip、Qualcomm、MTK、展锐等），自动检测 vendor、kernel 目录和 DTS 路径。

### 6.1 部署脚本

将本 skill 附带的两个脚本复制到项目中：

```bash
cp <skill_path>/scripts/bsp_filter_gen.py $BSP_ROOT/scripts/
cp <skill_path>/scripts/arg.sh $BSP_ROOT/scripts/
chmod +x $BSP_ROOT/scripts/bsp_filter_gen.py $BSP_ROOT/scripts/arg.sh
```

`<skill_path>` 是本 skill 所在目录。如脚本已存在则跳过。

### 6.2 生成索引

**传入项目的编译命令**（通过 `-b` 参数），脚本会自动：
- 从 `lunch` 参数解析 product name，自动检测 vendor
- 定位 `device/<vendor>/[<soc>/]<product>/BoardConfig.mk`，提取 DTS 和 defconfig 变量
- 遍历所有可能的 kernel 目录（`kernel/`、`kernel-5.10/`、`bsp/kernel/` 等）
- 递归追踪 DTS `#include` 链，只收录当前 target 实际引用的 DTS/DTSI
- 收集 defconfig、DTBO 模板、vendor common 配置等

```bash
cd $BSP_ROOT

# 传入用户的实际编译命令（lunch 部分是关键，后面的 make/build.sh 可选）
python3 scripts/bsp_filter_gen.py \
  -b "source build/envsetup.sh && lunch <product>-<variant>" \
  > .active_files.idx

# 不带 -b（仅 compdb + module-info，不追踪 DTS/defconfig）
python3 scripts/bsp_filter_gen.py > .active_files.idx
```

**多平台示例：**
```bash
# Rockchip
python3 scripts/bsp_filter_gen.py -b "source build/envsetup.sh && lunch rk3568_r-userdebug"
# Qualcomm
python3 scripts/bsp_filter_gen.py -b "source build/envsetup.sh && lunch lahaina-userdebug"
# MTK
python3 scripts/bsp_filter_gen.py -b "source build/envsetup.sh && lunch k6785v1_64-userdebug"
# 展锐
python3 scripts/bsp_filter_gen.py -b "source build/envsetup.sh && lunch sp9863a-userdebug"
```

**验证：**
```bash
wc -l .active_files.idx          # 典型值：3万~10万
grep "\.dts" .active_files.idx   # 应只含当前 target 的 DTS chain
grep "defconfig" .active_files.idx
```

### 6.3 配置快捷命令

`arg.sh` 会自动向上查找 `.active_files.idx`，在 BSP 任意子目录中均可使用。

```bash
# 在 .bashrc 或 .zshrc 中添加别名
alias arg='$BSP_ROOT/scripts/arg.sh'
```

**使用示例：**
```bash
arg "drm_bridge_attach"              # 只在活跃文件中搜索
arg -t c "dma_alloc_coherent"        # 限定 C 文件类型
arg "compatible.*<soc>" -g "*.dts"   # DTS 中搜索兼容字符串
```

### 6.4 索引更新时机

- 切换 lunch target 后需重新生成
- 增量编译后一般无需更新（compdb 和 module-info 不变）
- 新增/删除源文件后建议重新生成

---

## 常见问题

**Q: arg 搜索不到预期文件？**
确认 `.active_files.idx` 包含该文件：`grep <文件名> .active_files.idx`。若缺少，检查该文件是否在 compile_commands.json 或 module-info.json 中。若为 DTS/config 文件，确认生成索引时使用了 `--build-cmd`。

**Q: ninja 文件找不到？**
```bash
ls out/combined-*.ninja out/soong/build.ninja 2>/dev/null
```
若都不存在，说明还没编译，先执行编译命令。

**Q: gtags 建索引太慢？**
确认用了 `gtags -v -f gtags.files`（文件列表方式），而不是 `gtags -v`（扫描全目录）。排除 `out/` 可减少 80% 以上文件数。

**Q: OpenGrok MCP 调用失败？**
1. 检查容器：`docker compose ps`（需在 `opengrok/` 目录）
2. 检查 API：`curl http://localhost:8080/api/v1/configuration`
3. 降级方案：直接用 `global` 或 `rg`

**Q: clangd 崩溃或无法启动？**
确认 `.clangd` 中的 `Remove` 列表涵盖了你的平台专有 flags（通过 `head -5 compile_commands.json | grep -o '"command".*"'` 查看实际 flags）。
