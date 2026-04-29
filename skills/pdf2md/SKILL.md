---
name: pdf2md
description: Convert PDF files into Markdown using the Marker ML pipeline (layout detection + OCR + table + image extraction). Produces `<name>.md` next to each PDF and puts extracted figures/schematics into `<name>_images/`. Use this skill whenever the user asks to turn a PDF into Markdown, convert a PDF to MD for Obsidian/notes, batch-convert a folder of PDFs, "markerify" a PDF, or extract markdown text from PDFs — including Chinese phrases like "PDF 转 Markdown", "把这个 PDF 转成 MD", "批量转 PDF", "marker 跑一下". SKIP this skill (and suggest docsindex instead) when the PDF is a datasheet, schematic, pinout table, or any document where format fidelity matters more than Markdown-editability — Markdown loses too much structure for those, and full-text search via docsindex is the better tool.
---

# pdf2md

Thin wrapper around [Marker](https://github.com/VikParuchuri/marker) that converts PDFs to Markdown with image/table extraction. All dependencies live in `deps/.venv/` under this skill — nothing leaks into user project directories.

## First-time setup

```bash
bash $HOME/.claude/skills/pdf2md/deps/fetch_deps.sh   # download wheels (~3-5 GB)
bash $HOME/.claude/skills/pdf2md/deps/install.sh      # build deps/.venv
```

`pdf2md.py` auto-bootstraps: if you run it with the system `python3` and
`marker` isn't importable there, it re-execs into `deps/.venv/bin/python`
transparently. So both invocation styles work.

## When to use this skill

Use when the user wants **editable Markdown text** from a PDF for notes, Obsidian, RAG pipelines, or reading in a text editor:

- "把 SDK 手册转成 MD 我好加链接"
- "convert this whitepaper to markdown"
- "批量把这个目录下的教程 PDF 转了"
- "marker 跑一下这个 PDF"

## When NOT to use this skill

Markdown is lossy for visually-structured documents. **Suggest [`docsindex`](../docsindex/SKILL.md) instead** when:

- The PDF is a **datasheet**, **schematic**, **register map**, **pinout table**, **timing diagram**, or **mechanical drawing** — Markdown can't represent these faithfully; the user will get worse search results than they would from indexing the original PDF
- The user's goal is "search inside these PDFs" rather than "edit these as notes" — docsindex (MCP-based full-text search) is the right abstraction
- The PDFs are scanned without selectable text — Marker's OCR is decent but slow; confirm with the user before spending compute

If in doubt, ask: "Do you want to **edit this as notes** (→ Markdown) or **search inside it from Claude** (→ docsindex)?"

## How to invoke

Either way works — the script re-execs into the bundled venv if needed:

```bash
# Batch: convert every *.pdf under CWD (recursive), skipping existing .md outputs
python3 ~/.claude/skills/pdf2md/scripts/pdf2md.py

# Or via the bundled venv directly
~/.claude/skills/pdf2md/deps/.venv/bin/python \
  ~/.claude/skills/pdf2md/scripts/pdf2md.py

# Specific files
python3 ~/.claude/skills/pdf2md/scripts/pdf2md.py path/to/a.pdf path/to/b.pdf
```

The script reads from CWD (for batch mode) or from the absolute paths given, and writes each output **next to its source PDF**. It's idempotent — existing `<name>.md` is skipped, so re-running after adding new PDFs only processes the new ones.

## Output structure

For each `foo.pdf`:

```
foo.pdf
foo.md                  # Markdown with relative image links like ![](foo_images/img_0.png)
foo_images/             # Only created if the PDF had extractable figures
  img_0.png
  img_1.jpg
  ...
```

## Performance notes — tell the user these upfront before a batch run

- **CPU only on this machine** — no GPU. Expect ~1-5 min per medium PDF (dozens of pages with images). First run also loads models from disk (~10-20s extra).
- **First-ever invocation downloads ~2GB of models from HuggingFace** into `~/.cache/huggingface/`. The cache is shared, so subsequent runs (and other HF-using tools) reuse it.
- **Huge batches run for hours.** For a 100+ PDF batch, suggest running with `nohup` or a background shell and checking progress periodically rather than blocking Claude's main loop.
- **Failures don't stop the batch.** One bad PDF prints an `ERROR <name>: ...` line and moves on; exit code is 1 if any PDF failed.

## HuggingFace download tips

If the user is in China or HF is slow, suggest:

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

before running the script. After the first successful download, subsequent runs don't hit the network — they can set `HF_HUB_OFFLINE=1` to be safe.

## When it breaks

- `[ERROR] marker-pdf not available and bundled venv missing` → run `bash ~/.claude/skills/pdf2md/deps/install.sh`. The script tries to re-exec into `deps/.venv/` automatically; the error means that venv isn't built yet.
- `OSError: Can't load tokenizer for ...` or HF timeout → first-run model download hit a network issue. Retry with the HF mirror env var above.
- The output Markdown has garbled text where there should be figures → that's expected for raster figures; check the `<name>_images/` directory — the images are extracted separately and referenced from the MD.

## Why this skill is structured this way

- **Venv bundled under `deps/.venv/`**: the user explicitly wanted no tool dependencies in their docs vault. Putting the 5 GB venv here means deleting the skill cleans up everything; works from any CWD.
- **All deps in `deps/`** (matches `rk3568-flash` / `androidbsp-codeindex-setup` pattern): `requirements.txt` pins the exact wheel set, `fetch_deps.sh` populates `deps/wheels/` for offline rebuild, `install.sh` materializes `deps/.venv` from those wheels.
- **No CLI args beyond file paths**: Marker has dozens of knobs (force OCR, LLM refinement, page ranges, etc.). Exposing all of them bloats the skill; when a user actually needs them, edit `scripts/pdf2md.py` directly rather than wrapping flags.
- **Image refs use relative paths** (`foo_images/img_0.png` rather than absolute): so the output works regardless of where the user moves the MD — important for Obsidian and git.
