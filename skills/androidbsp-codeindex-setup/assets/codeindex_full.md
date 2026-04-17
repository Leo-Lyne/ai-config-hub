<!-- BEGIN: androidbsp-codeindex-setup v=2 -->
# Android BSP 代码检索规则（AI Agent 专用）

本文件由 `androidbsp-codeindex-setup` skill 部署。所有在此项目中工作的 AI agent
（Claude Code、Codex、Cursor、Antigravity 等）检索代码时必须遵守以下规则。

## 已配置的索引

| 资产 | 路径 | 用途 |
|---|---|---|
| `compile_commands.json` | 项目根 | clangd 语义索引（C/C++） |
| `GTAGS / GRTAGS / GPATH` | 项目根 | gtags 符号/引用数据库（C/C++/Java/Kotlin） |
| `.active_files.idx` | 项目根 | 当前 lunch target 实际编译到的源文件白名单 |
| `.clangd` | 项目根 | clangd flag 过滤（Android 专有 flag） |
| `scripts/arg.sh` | 项目内 | Active Ripgrep，基于 `.active_files.idx` 的过滤搜索 |

> 跨边界追踪（JNI / AIDL / HIDL / syscall / ioctl / `/dev` `/sys` `/proc`）**不归本套索引**，
> 交给姊妹 skill `androidbsp-codecross-setup`。

## 工具能力矩阵

| 工具 | C/C++ 符号 | Java/Kotlin | 文件名 | 全文/正则 | 设备树 | 跨文件引用 |
|---|---|---|---|---|---|---|
| `global` (gtags + universal-ctags) | ⭐⭐⭐ | ⭐⭐⭐ | ✗ | ⭐ | ✗ | ⭐⭐⭐ |
| `rg` (ripgrep) | ⭐⭐ | ⭐⭐ | ✗ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐ |
| `fd` | ✗ | ✗ | ⭐⭐⭐ | ✗ | ⭐⭐ | ✗ |
| `arg` (Active Ripgrep) | ⭐⭐⭐ | ⭐⭐⭐ | ✗ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐ |

## 已知盲区与降级策略

| 语言 | 索引覆盖 | 降级方式 |
|---|---|---|
| C / C++ | clangd 语义 + gtags 符号 | — |
| Java | gtags（universal-ctags） | — |
| Kotlin | **仅语法级**（无类型推断） | 跨文件类型问题 → rg 全文 + 手读源码 |
| Rust | **仅文本搜索**（无符号索引） | 任何 Rust 查询 → rg 为主 |
| AIDL/HIDL/DT/SELinux/Binder/etc. | 由 codecross / domaintrace 覆盖 | 见对应段落 |

## 按场景选工具

### 查找文件
```
fd <关键词> [目录]
```

### C/C++ 函数 / 结构体 / 宏 定义
```
global -d <symbol>          # 首选
global -s <partial>         # 模糊匹配
rg "^(static\s+)?(int|void|struct) <name>" --type c -l   # 备选
```

### 符号的所有引用（跨文件）
```
global -r <symbol>          # 首选；gtags 专长
rg "<symbol>" --type c      # 备选
```

### 宏定义
```
rg "^#define <MACRO>" --type h
global -d <MACRO>
```

### Java / Kotlin 类或方法
```
global -d <ClassName>
rg "class <Name>|fun <name>|void <name>" --type java --type kotlin
```

### 设备树节点 / 属性
```
rg "<node-name>|<property>" --glob "*.dts" --glob "*.dtsi"
fd -e dts -e dtsi "<关键词>" kernel/
```

### 全文 / 正则（仅搜当前 target 实际用到的文件）
```
arg "<pattern>"                          # 推荐：降噪、快
arg -t c "<pattern>"
arg "<pattern>" -g "*.dts"
rg "<pattern>" [目录] [--type c/java]    # 备选：全量
```

## 历史证据（events.jsonl）

`.codenav/events.jsonl` 是过去所有静态查询结果的累积日志。每行一条 JSON event，
schema 为 `androidbsp.event/v1`。

**新查询前先看历史**：

```bash
tail -n 200 .codenav/events.jsonl | jq -c 'select(.tags[] == "<topic>")'
```

合并多条 event（同 file + line + tag）的优先级：
1. `confidence: high` > `med` > `low`
2. `source` 以 `runtime-` 开头优先于 `static-`
3. 同 source 同 confidence，最新 `ts` 优先

**关闭日志写入**：日常调试不想污染日志时，给脚本加 `--no-events`。
**强制 JSON 输出**：脚本加 `--json` 改为输出 JSONL（每行一条 Finding）。

## 自动行为规则

1. 用户提到函数 / 结构体 / 宏 / 类名 / 文件名 → **直接搜，不要问**。
2. 第一个工具返回空 → **自动换下一个工具**（例如 gtags 没命中再试 rg）。
3. 有多个候选 → 列出 `文件路径:行号`，让用户挑。
4. 搜索 C/C++/Java/Kotlin 符号时：**优先 gtags**（有引用关系），不要上来就 rg。
5. 全文搜索时：**优先 `arg`**（只搜 active files），噪音少 3~10 倍。
6. 跨 JNI / AIDL / HIDL / syscall / ioctl 的查找 → 让用户跑 `androidbsp-codecross-setup`。

## 降级策略

```
gtags 数据库不存在（GTAGS/GRTAGS/GPATH 缺失）
  → 用 rg（始终可用）

Kotlin 符号 gtags 查不到
  → 检查 GTAGSLABEL=new-ctags 后重建；或 rg "class <Name>|fun <name>" --type kotlin

.active_files.idx 不存在
  → arg 自动降级到 rg

clangd 崩溃
  → 检查 .clangd 中 Remove 列表是否覆盖本平台的 flag
```

## 常见问题

**Q: `arg` 搜不到预期文件？**
确认 `.active_files.idx` 有它：`grep <文件名> .active_files.idx`。
缺了就重跑：`python3 scripts/bsp_filter_gen.py -b "<lunch 命令>" > .active_files.idx`。

**Q: 切换了 lunch target？**
重新跑 `bsp_filter_gen.py` 生成新的 `.active_files.idx`，gtags 也要重建
（`gtags.files` 来自 idx）。

**Q: 增量编译后要更新吗？**
一般不用。只有新增 / 删除源文件、或切 target 后才需要重建。
<!-- END: androidbsp-codeindex-setup -->
