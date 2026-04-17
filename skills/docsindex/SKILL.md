---
name: docsindex
description: Build, update, and link searchable tree indexes from PDF document collections. One MCP server per workspace exposes multiple doc collections via a scope list, so Claude can search them. Covers the full lifecycle: first-time init, incremental update, checkpoint resume, per-workspace linking, and MCP setup. Use this skill whenever the user wants to index PDFs, build or update a document knowledge base, make a PDF collection searchable, resume an interrupted index build, link/unlink a collection, or change a workspace's doc scope. Also trigger for Chinese phrases like "文档索引", "建索引", "PDF索引", "更新索引", "文档搜索", "索引中断", "续跑", "MCP配置", "link 文档库", "挂接文档库", "文档库 scope". When the user has a folder of technical PDFs (datasheets, SDK guides, hardware manuals) and wants Claude to be able to search them — this skill handles everything from first run to search-ready.
---

# docsindex

Full lifecycle management for PDF document collections. PageIndex-style, **vector-free**: extract each PDF's bookmark tree, use an LLM to enrich proprietary sections with searchable descriptions, save as JSON. No embeddings, no CUDA.

## Mental model

```
~/docs/
├── <collection-path>/*.pdf         # any nested structure under ~/docs
└── index/<collection-path>/        # index mirrors the docs structure
    └── *.json
```

A "collection path" is any subpath under `~/docs/`. Examples:
- `bsp-dev/atk-rk3568`
- `mtk-sdk/mt6789`
- `manuals/datasheets`

**Index mirrors docs structure** under `~/docs/index/`. One shared docs tree, one mirrored index tree, many workspaces can link different subsets.

**One MCP server per workspace** (named `docs`). Its scope is a list of collection paths from `~/docs/index/`. Use `link.py` to add/remove collections to this scope.

Scripts:
- `~/ai-config-hub/skills/docsindex/scripts/build_index.py` — build/update index
- `~/ai-config-hub/skills/docsindex/scripts/link.py` — manage workspace MCP scope

---

## Command routing

The skill dispatches on the first argument. **Run the scripts directly — do not ask the user to run them.**

| User invokes | You do |
|---|---|
| `/docsindex init <collection>` | Run `build_index.py init --docs-path ~/docs/<collection> --index-dir ~/docs/index/<collection> --model deepseek-chat` |
| `/docsindex setup <collection>` | Same as `init` (alias) |
| `/docsindex update <collection>` | Run `build_index.py update --docs-path ~/docs/<collection> --index-dir ~/docs/index/<collection> --model deepseek-chat` |
| `/docsindex verify <collection>` | Count JSONs, check screening distribution, report (see Step 3) |
| `/docsindex link <collection> [<collection2> …]` | Run `link.py <args>` from the current working directory |
| `/docsindex link --remove <collection>` | Run `link.py --remove <collection>` |
| `/docsindex link '*'` | Run `link.py '*'` |
| `/docsindex link --list` (or bare `/docsindex link`) | Run `link.py --list` |

**Conventions:**
- `<collection>` is a subpath under `~/docs/` (e.g., `bsp-dev/atk-rk3568`).
- Accept leading `/` — strip it (`/bsp-dev/atk-rk3568` and `bsp-dev/atk-rk3568` are equivalent).
- For `link`, the CWD where Claude is running IS the target workspace. Don't `cd` anywhere.
- For `init`/`setup`/`update`, do a pre-flight check (Step 1) before running.
- If interrupted, just re-run the same command — the script auto-resumes via `.partial.json`.

---

## Step 1 — Pre-flight checks

### 1a. Dependencies

```bash
python3 -c "import fitz, openai; print('OK')"
```

If missing: `pip install PyMuPDF openai`

### 1b. API key (default model: `deepseek-chat`)

Check presence only, **never print the value**:

```bash
[ -n "$DEEPSEEK_API_KEY" ] && echo "✓ DEEPSEEK_API_KEY set" || echo "✗ not set"
# or OPENAI_API_KEY / GEMINI_API_KEY depending on model
```

If missing: tell the user to `export <KEY>=...` (or add to `~/.zshrc`) and re-run.

### 1c. Resolve collection path

Ask the user for the collection subpath (e.g., `bsp-dev/atk-rk3568`). Compute:

```
DOCS_PATH  = ~/docs/<collection>
INDEX_DIR  = ~/docs/index/<collection>
```

Count PDFs and inform the user before starting — 500+ PDFs costs real tokens.

---

## Step 2 — Build / update the index

### `setup` or `init` — first-time build

```bash
python3 ~/ai-config-hub/skills/docsindex/scripts/build_index.py init \
  --docs-path ~/docs/<collection> \
  --index-dir ~/docs/index/<collection> \
  --model deepseek-chat
```

Defaults to 200 concurrent workers. Override with `--workers N` if API rate-limited.

**Cost guidance (deepseek-chat, ~800 PDFs):**
- Standards/specs (IEEE/USB/HDMI/MIPI/ARM/Linux): auto-detected, bookmark-only → free
- Vendor-specific (SoC datasheets, BSP guides, board tutorials): LLM-enhanced → ¥5–20 RMB total
- Wall time: 20–60 min with 200 workers

**Checkpoint / resume:** `.partial.json` files preserve progress. Re-run same command to resume. **Do not delete** `.partial.json` files or the `index-dir`.

### `update` — incremental sync

```bash
python3 ~/ai-config-hub/skills/docsindex/scripts/build_index.py update \
  --docs-path ~/docs/<collection> \
  --index-dir ~/docs/index/<collection> \
  --model deepseek-chat
```

Per-PDF behavior:

| Change | Action |
|---|---|
| New PDF | Index |
| PDF modified | Re-index |
| PDF removed | Delete stale `.json` |
| Unchanged | Skip |

---

## Step 3 — Verify the output

```bash
# Count completed index files (exclude .partial)
ls ~/docs/index/<collection>/*.json 2>/dev/null | grep -v '\.partial\.json' | wc -l

# Spot-check structure + screening distribution
python3 - <<'EOF'
import json, glob, os
files = [f for f in glob.glob(f"{os.path.expanduser('~')}/docs/index/<collection>/*.json") if '.partial.' not in f]
browse = sum(1 for f in files if json.load(open(f)).get('_meta', {}).get('mode') == 'browse_only')
full = sum(1 for f in files if json.load(open(f)).get('_meta', {}).get('mode') == 'full')
print(f"Total: {len(files)} | browse_only: {browse} | full: {full}")
EOF
```

---

## Step 4 — Link collections to a project workspace

**One MCP server per workspace.** The server's scope is a list of collections the workspace can search. Use `link.py` to modify the scope — it manages a single `docs` entry in `.mcp.json`, not one entry per collection.

### 4a. Check MCP server is installed

```bash
ls ~/.local/share/mcp-servers/docsindex/server.py && echo ✓ || echo ✗
python3 -c "import mcp" 2>/dev/null && echo "✓ mcp package installed" || echo "✗ install: pip3 install --user --break-system-packages mcp"
```

### 4b. Link operations

The user runs Claude from their workspace directory, then invokes the skill. Example user session:

```
# User is in /path/to/their/rk3568-project and invokes:
/docsindex link bsp-dev/atk-rk3568            # add one
/docsindex link bsp-dev/atk-rk3568 mtk-sdk/mt6789   # add multiple
/docsindex link '*'                            # expose all indexed collections
/docsindex link --remove mtk-sdk/mt6789        # remove
/docsindex link --list                         # show current scope
```

When invoked, you (Claude) run `link.py` in the current working directory. The script:
- Reads/writes a single `docs` server entry in `.mcp.json` at the workspace root
- Stores scope as comma-separated list in the `--scope` arg (or `*` for all)
- Validates that the collection index exists before adding
- Refuses to duplicate entries
- Removes the MCP entry entirely if scope becomes empty

**Run it as:**
```bash
python3 ~/ai-config-hub/skills/docsindex/scripts/link.py <args>
```

### 4c. Restart Claude Code

**Restart Claude Code** after changing scope. MCP servers load at startup, not hot-reloaded.

After restart, Claude gains these tools:

| Tool | What it does |
|---|---|
| `list_collections` | List all collections in scope, with doc counts |
| `list_documents(query?, collection?)` | List indexed docs, filter by keyword/collection |
| `browse_tree(doc_id, path?)` | Browse a PDF's chapter tree (doc_id = `<collection>/<filename>`) |
| `search_nodes(query, collection?, doc_id?)` | Search across scope, optional filter |
| `read_pages(doc_id, start, end)` | Read specific pages |

`collection` arg supports prefix matching: `bsp-dev` matches all `bsp-dev/*` collections.

---

## Supported models

| `--model` | Env var needed |
|---|---|
| `deepseek-chat` | `DEEPSEEK_API_KEY` |
| `deepseek-reasoner` | `DEEPSEEK_API_KEY` |
| `gpt-4o` | `OPENAI_API_KEY` |
| `gemini-2.0-flash` | `GEMINI_API_KEY` |

---

## Design notes

- **No vector DB** — tree + keyword search on LLM-enriched descriptions. No embeddings, no CUDA.
- **Pure LLM two-stage screening** — no hardcoded whitelists/vendor lists. `screen.py` asks the LLM: "Is this document published by the standards body that defines it, or by a product vendor that implements it?" Standards-body docs (IEEE/MIPI/USB-IF/ARM/JEDEC) → `browse_only` (bookmark tree only). Vendor docs (Rockchip/MediaTek/Qualcomm/board vendors/IC datasheets) → `full` (LLM-enhanced descriptions).
- **Mirror structure** — `~/docs/index/` mirrors `~/docs/` so collection paths are stable and shareable across workspaces.
- **One MCP per workspace** — scope determines which collections are accessible. One python process per workspace, not one per collection.
- **Doc IDs include collection path** — `bsp-dev/atk-rk3568/MIPI DSI Specification.pdf` — disambiguates same filename across collections.
- **Description quality = search ceiling** — if the LLM's description misses a term, `search_nodes` won't find it.
