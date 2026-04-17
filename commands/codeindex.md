---
description: 代码索引环境配置与检索（自动识别项目类型并路由到对应 skill）
argument-hint: [setup]
---

用户输入：`$ARGUMENTS`

## Step 1 — 探测项目类型

按以下优先级检查当前工作目录（用 Glob / Read，不要猜）：

1. **Android BSP**：存在 `build/envsetup.sh` + `device/` + `vendor/`，或 `.repo/`
2. **Linux BSP / Kernel 树**：顶层 `Makefile` 含 `VERSION =` `PATCHLEVEL =` + `Kbuild` + `arch/`
3. **ESP / 单片机**：`sdkconfig` + `CMakeLists.txt` 含 `idf_component_register`，或 `platformio.ini`
4. **App 项目**：
   - `package.json` → 前端 / Node
   - `pubspec.yaml` → Flutter
   - `app/build.gradle` 但无 `device/` → 纯 Android App

识别后先告知用户："检测到 XX 项目，将调用 YY skill"。
若模糊或同时命中多项，列出候选让用户确认，**不要猜**。

## Step 2 — 路由

| 项目类型 | 调用 Skill |
|---|---|
| Android BSP | `androidbsp-codeindex-setup` |
| Linux BSP | `linux-bsp-codeindex`（待建） |
| ESP / MCU | `mcu-codeindex`（待建） |
| App | `app-codeindex`（待建） |

若命中的 skill 尚未实现，明确告知用户缺失，**不要降级到其他 skill**。

## Step 3 — 子命令分派

解析 `$ARGUMENTS` 的首个 token：

### `setup`

路由到 Step 2 选出的 skill，由该 skill 全权负责后续环境配置流程。command 本身不介入具体步骤。

### 无参数 / 其他输入

路由到 Step 2 选出的 skill，视为检索请求，由该 skill 自行处理。

---

**重要**：所有操作必须通过 Skill 工具调用，不要绕过 skill 直接执行。
