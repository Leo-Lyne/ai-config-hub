#!/bin/bash

# AI Config Hub Sync Script
# 作用：一键同步 Skills、MCP 配置和项目规约到各个 AI Agent 的系统路径。

HUB_DIR="$HOME/ai-config-hub"
ANTI_SKILLS_DIR="$HOME/.gemini/antigravity/skills"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
CURSOR_SKILLS_DIR="$HOME/.cursor/skills"
CLAUDE_COMMANDS_DIR="$HOME/.claude/commands"

# 检查 Hub 目录
if [ ! -d "$HUB_DIR" ]; then
    echo "Error: $HUB_DIR not found!"
    exit 1
fi

echo "--- 正在同步 AI Skills ---"

# 确保目标 Skill 目录存在
mkdir -p "$ANTI_SKILLS_DIR" "$CLAUDE_SKILLS_DIR" "$CURSOR_SKILLS_DIR"

# 同步自定义 Skills
for skill_dir in "$HUB_DIR/skills"/*/; do
    name=$(basename "$skill_dir")
    echo "Linking skill: $name"
    # 如果目标是目录且不是软链接，则删除它
    [ -d "$ANTI_SKILLS_DIR/$name" ] && [ ! -L "$ANTI_SKILLS_DIR/$name" ] && rm -rf "$ANTI_SKILLS_DIR/$name"
    [ -d "$CLAUDE_SKILLS_DIR/$name" ] && [ ! -L "$CLAUDE_SKILLS_DIR/$name" ] && rm -rf "$CLAUDE_SKILLS_DIR/$name"
    
    ln -sfn "$skill_dir" "$ANTI_SKILLS_DIR/$name"
    ln -sfn "$skill_dir" "$CLAUDE_SKILLS_DIR/$name"
done

# 同步社区仓库中的 Skills (antigravity-skills)
if [ -d "$HUB_DIR/repos/antigravity-skills/skills" ]; then
    for skill_dir in "$HUB_DIR/repos/antigravity-skills/skills"/*/; do
        name=$(basename "$skill_dir")
        # echo "Linking community skill: $name"
        ln -sfn "$skill_dir" "$ANTI_SKILLS_DIR/$name"
    done
fi

echo "--- 正在同步 Slash Commands ---"

if [ -d "$HUB_DIR/commands" ]; then
    mkdir -p "$CLAUDE_COMMANDS_DIR"
    for cmd_file in "$HUB_DIR/commands"/*.md; do
        [ -f "$cmd_file" ] || continue
        name=$(basename "$cmd_file")
        echo "Linking command: /$name"
        ln -sfn "$cmd_file" "$CLAUDE_COMMANDS_DIR/$name"
    done
fi

echo "--- 正在同步 MCP 配置 ---"

MASTER_MCP="$HUB_DIR/mcp/master_mcp.json"
if [ -f "$MASTER_MCP" ]; then
    MCP_SERVERS=$(jq '.mcpServers' "$MASTER_MCP")

    # Antigravity — 纯 MCP 配置，可以直接软链接
    mkdir -p "$HOME/.gemini/antigravity"
    ln -sfn "$MASTER_MCP" "$HOME/.gemini/antigravity/mcp_config.json"

    # Claude Code — ~/.claude.json 含运行时状态，只合并 mcpServers 字段
    CLAUDE_JSON="$HOME/.claude.json"
    if [ -L "$CLAUDE_JSON" ]; then
        # 如果是软链接（旧方式），先转为独立文件
        LINK_CONTENT=$(cat "$CLAUDE_JSON")
        rm "$CLAUDE_JSON"
        echo "$LINK_CONTENT" > "$CLAUDE_JSON"
    fi
    if [ -f "$CLAUDE_JSON" ]; then
        jq --argjson servers "$MCP_SERVERS" '.mcpServers = $servers' "$CLAUDE_JSON" > "${CLAUDE_JSON}.tmp" \
            && mv "${CLAUDE_JSON}.tmp" "$CLAUDE_JSON"
    else
        echo "{\"mcpServers\": $MCP_SERVERS}" > "$CLAUDE_JSON"
    fi

    # Cursor
    mkdir -p "$HOME/.cursor"
    ln -sfn "$MASTER_MCP" "$HOME/.cursor/mcp.json"

    echo "✓ MCP 配置已同步到 Antigravity, Claude Code (merge), Cursor"
fi

echo "--- 正在同步全局规则 ---"

RULES_HUB="$HUB_DIR/rules"
mkdir -p "$HOME/.gemini"
ln -sfn "$RULES_HUB/universal-agent-rules.md" "$HOME/.gemini/AGENTS.md"
ln -sfn "$RULES_HUB/gemini-native-rules.md" "$HOME/.gemini/GEMINI.md"

echo "✓ 全局通用规则已同步到 ~/.gemini/"

echo "--- 同步完成！ ---"
echo "提示：请重启你的 AI Agent 以应用最新配置。"
