---
name: android-bsp-codesearch
description: 'Android BSP 项目代码检索决策指南。当 AI Agent 在 BSP 项目中需要搜索代码、查找符号/函数/结构体/宏定义、查找设备树节点、查找 HAL/AIDL 接口时，使用此 skill 选择最优工具和策略。适用于用户说"帮我找函数"、"搜一下这个结构体"、"这个宏在哪定义的"等场景。'
---

# android-bsp-codesearch — Android BSP 代码检索决策指南

## 前提条件

1. Android BSP项目代码导航环境配置完成（可按android-bsp-codenav-setup skill进行验证）

**所有 AI Agent（Claude Code、Opencode、Cursor、Codex、Antigravity .etc）在 BSP 项目中搜索代码时应遵守以下规则。**

## 工具能力矩阵

| 工具 | C/C++ 符号 | Java/Kotlin | 文件名 | 全文/正则 | 设备树 | 跨文件引用 |
|------|-----------|-------------|--------|-----------|--------|-----------|
| `global` (gtags) | ⭐⭐⭐ | ⭐⭐ | ✗ | ⭐ | ✗ | ⭐⭐⭐ |
| `rg` (ripgrep) | ⭐⭐ | ⭐⭐ | ✗ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐ |
| `fd` | ✗ | ✗ | ⭐⭐⭐ | ✗ | ⭐⭐ | ✗ |
| `readtags` (ctags) | ⭐⭐ | ⭐ | ✗ | ✗ | ✗ | ✗ |
| `locate` | ✗ | ✗ | ⭐⭐⭐ | ✗ | ✗ | ✗ |
| `arg` (Active Ripgrep) | ⭐⭐⭐ | ⭐⭐⭐ | ✗ | ⭐⭐⭐ | ⭐⭐⭐ | ⭐ |
| OpenGrok MCP | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐ |

## 按场景选择工具（优先级从高到低）

### 查找文件
```
fd <名称关键词> [目录]          # 首选：快速文件名搜索
locate <文件名>                 # 备选：全局路径搜索（数据库可能不是最新）
```
> 示例：`fd "defconfig" kernel/arch` 或 `fd "<product>.mk" device/`

### 查找 C/C++ 函数/结构体/宏 定义
```
global -d <symbol>              # 首选：精确定义，支持 C/C++/Java
global -s <partial>             # 模糊匹配符号名
rg "^(static\s+)?(int|void|struct) <name>" --type c -l   # 备选：正则匹配函数签名
```
> 示例：`global -d drm_bridge_attach` 或 `global -d camera_provider_init`

### 查找符号的所有引用
```
global -r <symbol>              # 首选：交叉引用，gtags 专长
rg "<symbol>" --type c          # 备选：全文正则
```

### 查找宏定义
```
rg "^#define <MACRO>" --type h  # 首选：宏在头文件中，rg 最直接
global -d <MACRO>               # 备选
```

### 查找 Java/Kotlin 类或方法
```
global -d <ClassName>           # 首选：gtags 支持 Java
rg "class <Name>|fun <name>|void <name>" --type java --type kotlin
```
> 示例：`global -d ActivityThread`

### 查找设备树节点/属性
```
rg "<node-name>|<property>" --glob "*.dts" --glob "*.dtsi"
fd -e dts -e dtsi "<关键词>" kernel/arch
```
> 示例：`rg "compatible.*<soc>" --glob "*.dtsi"` （如 `rg "compatible.*sm8350"`, `rg "compatible.*mt6785"`）

### 查找 HAL/AIDL/HIDL 接口
```
fd -e aidl -e hal <关键词>      # 找接口文件
rg "interface <Name>" --glob "*.aidl" --glob "*.hal"
```

### 全文/正则搜索
```
rg "<pattern>" [目录] [--type c/java/cpp]
rg "<pattern>" -g "*.{c,h,cpp,java}"
```

### 模糊交互式查找
```
fd <关键词> | fzf               # 文件名模糊
global -s <partial> | fzf       # 符号名模糊
```

### 跨文件语义搜索（需 OpenGrok 容器运行）
```
mcp__opengrok__search_opengrok("<symbol>", "def")   # 定义
mcp__opengrok__search_opengrok("<symbol>", "ref")   # 引用
```

## 降级策略

```
OpenGrok MCP 不可用（Docker 未运行）
  → global (gtags) + rg

gtags 数据库不存在
  → rg + readtags

所有索引都不存在
  → rg（始终可用，无需索引）
```

## 自动行为规则

- 用户提到函数名、结构体、宏、文件名 → **直接搜索，不询问确认**
- 第一个工具结果为空 → **自动换下一个工具重试**
- 找到多个候选 → **列出文件路径+行号，让用户确认**
- OpenGrok 容器状态未知时 → **先用 global/rg，不要等待确认 Docker 状态**
