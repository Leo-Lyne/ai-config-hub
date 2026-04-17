# Claude Code Hooks

自动化触发器配置，用于优化 Claude Code 工作流。

## auto_model_router

自动根据 prompt 复杂度选择合适的 Claude 模型（Haiku/Sonnet/Opus），用于优化配额消耗。

### 功能

- **智能模型选择**：基于 prompt 长度、关键词、代码块等自动分类任务复杂度
- **配额优化**：简单任务用 Haiku（速度快），复杂任务用 Opus（能力强）
- **多后端支持**：支持 heuristic（本地）、ollama、deepseek、groq
- **disabled think 注入**：禁用自动 think 模式，避免额度浪费

### 使用

#### 1. 复制文件到 Claude 配置目录

```bash
cp auto_model_router.py ~/.claude/
cp stop_summary.py ~/.claude/
cp router.conf ~/.claude/
chmod +x ~/.claude/auto_model_router.py ~/.claude/stop_summary.py
```

#### 2. 配置 settings.json 中的 hooks

在 `~/.claude/settings.json` 的 `hooks` 字段添加：

```json
"hooks": {
  "UserPromptSubmit": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "bash -l -c 'python3 /home/leo/.claude/auto_model_router.py'"
        }
      ]
    }
  ],
  "Stop": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "python3 /home/leo/.claude/stop_summary.py"
        }
      ]
    }
  ]
}
```

#### 3. 配置路由后端（可选）

编辑 `~/.claude/router.conf`：

```bash
# 本地启发式路由（推荐，无额外 API 调用）
CLAUDE_ROUTER_BACKEND=heuristic

# 或使用 Ollama 本地模型分类
CLAUDE_ROUTER_BACKEND=ollama
CLAUDE_ROUTER_OLLAMA_MODEL=qwen2.5:0.5b

# 或使用外部 API
CLAUDE_ROUTER_BACKEND=deepseek  # 需要 DEEPSEEK_API_KEY
CLAUDE_ROUTER_BACKEND=groq      # 需要 GROQ_API_KEY
```

### 工作流

1. **UserPromptSubmit 事件**：用户提交 prompt 时触发
   - `auto_model_router.py` 分析 prompt 复杂度
   - 选择合适的模型（haiku/sonnet/opus）
   - 关闭自动 think 注入（保护 Opus 配额）
   - 将路由决策写入 `/tmp/claude_router_<session_id>.json`

2. **Stop 事件**：Claude 回复完成时触发
   - `stop_summary.py` 读取路由状态文件
   - 显示 `[router] backend → model` 消息
   - 帮助用户观察路由效果

### 配额节省效果

对于 Claude.ai Max 20x ($100/月)：

- Opus 每周限制：~24-40 小时（极快被耗尽）
- Sonnet 每周限制：~240-480 小时（充足）
- Haiku 每周限制：~无限制（快速任务）

**路由优势**：通过自动将简单任务分发到 Haiku/Sonnet，保留 Opus 额度用于真正需要深度推理的任务。

### 模型分类规则

#### Haiku（快速、轻量）
- 字数 < 12，无代码块，无关键词
- 示例：`What's 2+2?`

#### Sonnet（均衡）
- 字数 12-120，或有适量代码
- 示例：`Explain how React hooks work`

#### Opus（深度推理）
- 字数 > 120，或多个复杂关键词
- 包含完整架构问题、系统设计
- 示例：`Design a microservices architecture with...`

### 禁用 think 注入

`classify_think()` 函数被禁用，始终返回 `None`。

**原因**：
- `think hard` 和 `ultrathink` 大幅增加 Opus 额度消耗
- 对于大多数编码任务，Sonnet 已足够
- 用户可手动在 prompt 中加入 "think hard" 如需强制启用

### 调试

查看路由决策：

```bash
echo '{"session_id":"test","user_prompt":"Fix this complex bug in the kernel module"}' | python3 ~/.claude/auto_model_router.py
```

查看停止时的路由信息：

```bash
echo '{"session_id":"test"}' | python3 ~/.claude/stop_summary.py
```

### 已知限制

1. **启发式后端准确性**：某些边界情况可能误分类
2. **没有官方保证**：Anthropic 没有明确说明 Opus 额度机制
3. **社区测试**：配额限制数据基于用户实际体验，非官方文档

## 替代方案

- **opusplan**（官方）：`/model opusplan` 在 Plan 阶段用 Opus，Implementation 用 Sonnet
- **手动切换**：`/model haiku|sonnet|opus` 用户手动选择

## 许可证

MIT
