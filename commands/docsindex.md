---
description: PDF 文档树索引全生命周期管理（路由到 docsindex skill）
argument-hint: [setup|update] [--docs-path <path>] [--collection <name>]
---

用户输入：`$ARGUMENTS`

## Step 1 — 解析子命令

解析 `$ARGUMENTS` 的首个 token：

| 子命令 | 含义 |
|---|---|
| `setup` | 全量初始化，对指定目录下所有 PDF 建树索引 |
| `update` | 增量更新，只处理新增/修改/删除的 PDF |
| 无参数 / 其他 | 视为检索请求，路由到 docsindex 处理 |

## Step 2 — 路由

所有子命令统一路由到 `docsindex` skill，由该 skill 全权处理。

**重要**：必须通过 Skill 工具调用，不要绕过 skill 直接执行脚本。
