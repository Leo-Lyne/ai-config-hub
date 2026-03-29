# Global AI Rules (Universal)

这是我的全局行为准则，适用于所有项目类型（Android, Linux, ESP32, Unity, etc.）。

## 1. 沟通偏好 (Communication)
- **语言**：始终使用简体中文与用户交流。
- **风格**：专业、简洁、直接。

## 2. 工程方法论 (Methodology)
- **调试**：优先调用 `systematic-debugging` 技能，严禁在未定位根因前盲目修补。
- **编码**：遵循 `professional-coding` 技能中的最佳实践。
- **验证**：所有代码变更必须经过验证（测试运行、编译检查或静态分析）。

## 3. 工具使用 (Tools & MCP)
- **资源受限环境**：在嵌入式项目（如 ESP32）中，优先考虑内存和功耗。
- **索引工具**：在大型项目中，优先查找项目根目录下的索引数据库（GTAGS, tags）。
- **MCP**：支持项目本地 `.mcp.json` 覆盖全局配置。

## 4. 安全与规范
- 未经许可不得删除用户数据。
- 敏感信息（API Key, 密码）严禁硬编码。
