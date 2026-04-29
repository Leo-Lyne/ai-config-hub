# pdf2md / deps

Self-contained Python venv for the `/pdf2md` skill — bundled `marker-pdf` +
`torch` + `transformers` + `surya-ocr` and their pinned transitive deps.

## Bootstrap

```bash
# (online machine) Download all wheels (~3-5 GB on Linux x86_64) into deps/wheels/
bash ~/.claude/skills/pdf2md/deps/fetch_deps.sh

# (any machine, online or offline) Build deps/.venv from those wheels
bash ~/.claude/skills/pdf2md/deps/install.sh
```

If `deps/.venv/` already exists with `marker` importable, both scripts no-op. The
existing venv is reused.

## What's bundled

| Path | Size | Why |
|---|---|---|
| `deps/requirements.txt` | ~3 KB | Full pip-freeze of the validated wheel set. Updated by `fetch_deps.sh`. |
| `deps/requirements.in` | <1 KB | Top-level (`marker-pdf`). For when you want to bump and re-resolve. |
| `deps/wheels/*.whl` | ~3-5 GB | Offline-installable wheels. **gitignored** — too big for git. |
| `deps/.venv/` | ~5 GB (created locally) | The actual interpreter. Never committed. |

## How `pdf2md.py` finds the venv

The script tries `import marker` under whatever Python invoked it; if the import
fails, it `os.execv`s into `deps/.venv/bin/python` (auto-detected). So:

```bash
python3 ~/.claude/skills/pdf2md/scripts/pdf2md.py file.pdf      # works (re-execs)
~/.claude/skills/pdf2md/deps/.venv/bin/python …pdf2md.py …      # works (direct)
```

## Why bundle here instead of pip-installing system-wide

- Marker's CUDA libs + torch take ~3 GB. Polluting the system Python is rude.
- The user explicitly wanted **no tool dependencies inside their docs vault**.
  `deps/.venv/` keeps everything inside the skill — `rm -rf ~/.claude/skills/pdf2md`
  cleans up cleanly.
- Reproducibility: pinned `requirements.txt` + bundled `wheels/` = identical
  environment on a new machine, including offline.

## Refresh the pin

```bash
# Edit requirements.in if you want to add or change top-levels
~/.claude/skills/pdf2md/deps/.venv/bin/python -m pip install --upgrade pip-tools
~/.claude/skills/pdf2md/deps/.venv/bin/python -m piptools compile \
    --output-file ~/.claude/skills/pdf2md/deps/requirements.txt \
    ~/.claude/skills/pdf2md/deps/requirements.in
bash ~/.claude/skills/pdf2md/deps/fetch_deps.sh   # re-fetch matching wheels
bash ~/.claude/skills/pdf2md/deps/install.sh
```
