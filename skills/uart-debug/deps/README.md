# uart-debug / deps

Self-contained Python venv with `pyserial` for the `/uart-debug` skill.

## Bootstrap

Two-step (online machine, then any machine):

```bash
# (online) download pyserial wheel into deps/wheels/
bash ~/.claude/skills/uart-debug/deps/fetch_deps.sh

# (any) build deps/.venv from the bundled wheels
bash ~/.claude/skills/uart-debug/deps/install.sh
```

Or one-shot if you already have `deps/wheels/*.whl` committed:

```bash
bash ~/.claude/skills/uart-debug/deps/install.sh
```

## What gets bundled

| Path | Size | Why |
|---|---|---|
| `deps/wheels/pyserial-*.whl` | ~85 KB | The only runtime dep `uart.py` imports. |
| `deps/.venv/` | ~15 MB (created locally) | Reproducible interpreter — never committed. |

## Resolution order in `uart.py`

1. Try ambient `import serial` (works if user already has pyserial system-wide)
2. If missing, look for `deps/.venv/bin/python` and `os.execv` into it (transparent)
3. If neither — print install hint and exit

This means: if you bundled the venv via `deps/install.sh`, the script Just Works
regardless of what Python the user invokes it with.
