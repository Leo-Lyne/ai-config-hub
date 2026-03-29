# Global Gemini Rules (Universal)

这是 Antigravity (Gemini) 的全局原生准则。

## 1. 核心指令
- **语言**：始终使用简体中文。
- **模式**：优先进入 AGENT 模式执行任务。
- **任务边界**：复杂任务必须使用 `task_boundary` 进行阶段性汇报。

## 2. 技能联动
- **调试**：自动调用 `systematic-debugging` 技能。
- **编码**：自动调用 `professional-coding` 技能。

## 3. 环境适配
- 如果检测到 Makefile/CMakeLists.txt，优先使用对应的构建系统进行分析。
- 如果检测到 `.mcp.json`，自动加载本地 MCP 服务。

## 4. 输出规范
- 修复建议必须包含“原理分析 - 修复方案 - 验证步骤”。
