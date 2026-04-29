#!/usr/bin/env python3
"""Link/unlink docs collections to the current project workspace.

One MCP server entry per workspace (named "docs" by default). Each workspace has
a scope list of collection paths; this script adds/removes entries from that scope.

Usage:
    # Add a collection to current workspace's docs scope
    cd /path/to/project
    python link.py bsp-dev/atk-rk3568

    # Add multiple at once
    python link.py bsp-dev/atk-rk3568 mtk-sdk/mt6789

    # Remove a collection from scope
    python link.py --remove bsp-dev/atk-rk3568

    # Show current scope
    python link.py --list

    # Use '*' to expose all collections under ~/docs/.docsindex
    python link.py '*'
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


DOCS_ROOT = Path.home() / "MyLibrary"
INDEX_ROOT = DOCS_ROOT / ".docsindex"
SERVER_PATH = Path.home() / ".local/share/mcp-servers/docsindex/server.py"
DEFAULT_SERVER_NAME = "docs"


def _read_mcp_config(mcp_json: Path) -> dict:
    if not mcp_json.exists():
        return {}
    try:
        return json.loads(mcp_json.read_text())
    except Exception as e:
        print(f"Error: cannot parse {mcp_json}: {e}", file=sys.stderr)
        sys.exit(1)


def _get_scope_list(config: dict, server_name: str) -> list[str]:
    """Parse the current scope from --scope arg in the server config."""
    servers = config.get("mcpServers", {})
    if server_name not in servers:
        return []
    args = servers[server_name].get("args", [])
    try:
        idx = args.index("--scope")
    except ValueError:
        return []
    if idx + 1 >= len(args):
        return []
    scope_str = args[idx + 1]
    if scope_str == "*":
        return ["*"]
    return [s.strip() for s in scope_str.split(",") if s.strip()]


def _write_config(mcp_json: Path, config: dict, server_name: str, scope: list[str]) -> None:
    """Write the docs MCP entry with the given scope."""
    if not scope:
        # Remove the server entry entirely if scope is empty
        if "mcpServers" in config and server_name in config["mcpServers"]:
            del config["mcpServers"][server_name]
        # Clean up empty mcpServers key
        if "mcpServers" in config and not config["mcpServers"]:
            del config["mcpServers"]
    else:
        scope_str = ",".join(scope) if scope != ["*"] else "*"
        config.setdefault("mcpServers", {})[server_name] = {
            "command": "python3",
            "args": [
                str(SERVER_PATH),
                "--root-dir", str(INDEX_ROOT),
                "--scope", scope_str,
            ],
        }

    if config:
        mcp_json.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    elif mcp_json.exists():
        mcp_json.unlink()


def _validate_collection(collection: str) -> Path:
    collection = collection.strip().strip("/")
    path = INDEX_ROOT / collection
    if not path.exists():
        print(f"Error: index not found at {path}", file=sys.stderr)
        print(f"Run first: /docsindex setup {collection}", file=sys.stderr)
        sys.exit(1)
    # Check there's at least one non-partial json anywhere under it
    has_json = any(
        ".partial." not in jp.name
        for jp in path.rglob("*.json")
    )
    if not has_json:
        print(f"Error: no index JSON files under {path}", file=sys.stderr)
        sys.exit(1)
    return path


def _build_context_content(collection: str) -> str:
    """Full docs MCP guidance — written to .claude/contexts/docsindex.md (hook-injected on demand)."""
    if collection == "*":
        search_example = "search_nodes(query)"
        col_hint = "（所有已索引集合）"
    else:
        search_example = f'search_nodes(query, collection="{collection}")'
        col_hint = f"— {collection}"
    return (
        f"# 文档索引（docs MCP）{col_hint}\n\n"
        f"遇到硬件/协议/接口/datasheet 相关问题时，优先调用 docs MCP 工具查阅文档索引：\n\n"
        f"- `{search_example}` — 全文搜索\n"
        f"- `browse_tree(doc_id)` — 浏览 PDF 章节树\n"
        f"- `read_pages(doc_id, start, end)` — 读具体页内容\n\n"
        f"搜索策略：先用 `search_nodes` 定位章节，再用 `browse_tree` 确认层级，最后 `read_pages` 读详细内容。\n"
    )


def _build_agents_stub(collection: str) -> str:
    """Minimal stub injected into AGENTS.md — full guidance is hook-injected from contexts/."""
    marker = f"docsindex:{collection}"
    col_hint = "所有已索引集合" if collection == "*" else collection
    return (
        f"<!-- {marker} -->\n"
        f"## docs MCP — {col_hint}\n"
        f"已链接文档索引。hardware/protocol/datasheet 问题时 hook 自动注入完整使用指引。\n"
        f"<!-- /{marker} -->"
    )


def _inject_agents_block(workspace: Path, collection: str) -> None:
    # 1. Write full guidance to .claude/contexts/docsindex.md (hook reads this)
    ctx_dir = workspace / ".claude" / "contexts"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    ctx_file = ctx_dir / "docsindex.md"
    ctx_file.write_text(_build_context_content(collection))
    print(f"  ✓ Written docs MCP guidance to {ctx_file}")

    # 2. Write minimal stub to AGENTS.md so other AI tools know the capability exists
    agents_md = workspace / "AGENTS.md"
    marker = f"<!-- docsindex:{collection} -->"
    stub = _build_agents_stub(collection)

    if agents_md.exists():
        content = agents_md.read_text()
        if marker in content:
            print(f"  AGENTS.md: stub for '{collection}' already present, skipped.")
            return
        content = content.rstrip() + "\n\n" + stub + "\n"
    else:
        content = stub + "\n"

    agents_md.write_text(content)
    print(f"  ✓ Injected stub into {agents_md}")


def _remove_agents_block(workspace: Path, collection: str) -> None:
    # 1. Remove context file
    ctx_file = workspace / ".claude" / "contexts" / "docsindex.md"
    if ctx_file.exists():
        ctx_file.unlink()
        print(f"  ✓ Removed {ctx_file}")

    # 2. Remove stub from AGENTS.md
    agents_md = workspace / "AGENTS.md"
    if not agents_md.exists():
        return

    marker_open = f"<!-- docsindex:{collection} -->"
    marker_close = f"<!-- /docsindex:{collection} -->"
    content = agents_md.read_text()

    if marker_open not in content:
        return

    pattern = rf"\n*{re.escape(marker_open)}.*?{re.escape(marker_close)}\n?"
    new_content = re.sub(pattern, "\n", content, flags=re.DOTALL).rstrip()
    if new_content:
        agents_md.write_text(new_content + "\n")
        print(f"  ✓ Removed stub for '{collection}' from {agents_md}")
    else:
        agents_md.unlink()
        print(f"  ✓ Removed stub for '{collection}'; AGENTS.md now empty, deleted.")


def main():
    parser = argparse.ArgumentParser(
        description="Link/unlink docs collections to the current workspace"
    )
    parser.add_argument(
        "collections", nargs="*",
        help="Collection paths relative to ~/docs (e.g., bsp-dev/atk-rk3568). "
             "Use '*' to expose all indexed collections."
    )
    parser.add_argument("--remove", "-r", action="store_true",
                        help="Remove the given collections from scope")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List current scope and exit")
    parser.add_argument("--name", default=DEFAULT_SERVER_NAME,
                        help=f"MCP server name (default: {DEFAULT_SERVER_NAME})")
    parser.add_argument("--workspace", default=".",
                        help="Workspace directory (default: current)")
    args = parser.parse_args()

    if not SERVER_PATH.exists():
        print(f"Error: MCP server not installed at {SERVER_PATH}", file=sys.stderr)
        sys.exit(1)

    workspace = Path(args.workspace).resolve()
    mcp_json = workspace / ".mcp.json"
    config = _read_mcp_config(mcp_json)
    current_scope = _get_scope_list(config, args.name)

    # --list: show current state
    if args.list or (not args.collections and not args.remove):
        if not current_scope:
            print(f"No '{args.name}' MCP server configured in {mcp_json}")
            return
        print(f"Workspace: {workspace}")
        print(f"MCP server: {args.name}")
        print(f"Current scope ({len(current_scope)} collection(s)):")
        for c in current_scope:
            print(f"  - {c}")
        return

    if not args.collections:
        print("Error: no collections specified", file=sys.stderr)
        sys.exit(1)

    # Normalize input collections
    requested = [c.strip().strip("/") for c in args.collections if c.strip()]

    # Handle the wildcard specially
    if "*" in requested:
        if args.remove:
            print("Error: cannot --remove with '*'. Specify collections explicitly.", file=sys.stderr)
            sys.exit(1)
        new_scope = ["*"]
        action = "Set"
        changed: list[str] = ["*"]
    else:
        # Validate each collection exists
        if not args.remove:
            for c in requested:
                _validate_collection(c)

        # If current scope is "*" and user adds a specific collection, switch to explicit mode
        # (treat "*" as if all collections were already listed? simpler: refuse)
        if current_scope == ["*"]:
            if args.remove:
                print("Error: current scope is '*'. To remove specific collections, "
                      "first set an explicit scope.", file=sys.stderr)
                sys.exit(1)
            # Just stay at "*" — already includes everything
            print("Current scope is '*' (all collections); no change needed.")
            return

        new_scope = list(current_scope)
        changed = []
        skipped = []

        if args.remove:
            for c in requested:
                if c in new_scope:
                    new_scope.remove(c)
                    changed.append(c)
                else:
                    skipped.append(c)
            action = "Removed" if changed else "No changes"
        else:
            for c in requested:
                if c in new_scope:
                    skipped.append(c)
                else:
                    new_scope.append(c)
                    changed.append(c)
            action = "Added" if changed else "No changes"

        if skipped:
            verb = "not in scope" if args.remove else "already in scope"
            print(f"Skipped ({verb}): {', '.join(skipped)}")
        if not changed:
            return

    _write_config(mcp_json, config, args.name, new_scope)

    if action == "Set":
        print(f"✓ Set scope to '*' (all collections) in {mcp_json}")
    else:
        print(f"✓ {action} {len(changed)} collection(s) in {mcp_json}")

    if new_scope:
        print(f"  Current scope ({len(new_scope)}):")
        for c in new_scope:
            print(f"    - {c}")
    else:
        print(f"  Scope now empty; removed MCP server entry '{args.name}'.")

    # Sync AGENTS.md
    if args.remove:
        for c in changed:
            _remove_agents_block(workspace, c)
    elif action == "Set":  # wildcard
        _inject_agents_block(workspace, "*")
    else:
        for c in changed:
            _inject_agents_block(workspace, c)
    # If scope became empty, also clean up wildcard block if present
    if not new_scope:
        _remove_agents_block(workspace, "*")

    print(f"\n⚠  Restart Claude Code to reload the MCP server.")


if __name__ == "__main__":
    main()
