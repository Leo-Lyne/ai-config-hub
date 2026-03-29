## 代码检索规则（自动执行，无需询问）

这是一个 Android BSP 项目，已配置完整的代码导航环境。
当需要查找、定位或搜索代码时，直接调用以下工具，不询问用户，不等待确认。

### 按场景选择工具

| 场景 | 首选工具 | 备选工具 |
|------|----------|----------|
| 查找文件 | `fd <名称> [目录]` | `locate <名称>` |
| C/C++ 函数/结构体定义 | `global -d <symbol>` | `rg "函数签名" --type c` |
| 符号所有引用 | `global -r <symbol>` | `rg <symbol> --type c` |
| 宏定义 | `rg "^#define <MACRO>" --type h` | `global -d <MACRO>` |
| Java/Kotlin 类方法 | `global -d <ClassName>` | `rg "class <Name>" --type java` |
| 设备树节点/属性 | `rg <pattern> --glob "*.dts" --glob "*.dtsi"` | `fd -e dts -e dtsi` |
| HAL/AIDL 接口 | `fd -e aidl -e hal <关键词>` | `rg "interface" --glob "*.aidl"` |
| 全文正则搜索 | `rg <pattern> [--type c/java/cpp]` | — |
| 跨文件语义搜索 | `mcp__opengrok__search_opengrok` (需 Docker) | `global -r` |

### 降级策略

- OpenGrok MCP 不可用 → 自动用 `global` + `rg`
- gtags 数据库不存在 → 用 `rg` + `readtags`
- 第一个工具结果为空 → 自动换下一个工具，不询问

### 环境信息

- BSP Root: 此文件所在目录
- compile_commands.json: 项目根目录（供 clangd 使用）
- gtags 数据库: GTAGS / GRTAGS / GPATH（项目根目录）
- OpenGrok MCP: .mcp.json 已配置，`docker compose up -d` 启动
