# adb / deps

Self-contained Android platform-tools (adb / fastboot) for the `/adb` skill.

## Bootstrap

```bash
bash ~/.claude/skills/adb/deps/fetch_deps.sh
```

Downloads `platform-tools-latest-<linux|darwin|windows>.zip` (~30 MB) from
Google's official mirror, extracts to `deps/platform-tools/`. Idempotent —
re-runs are no-ops.

## What gets bundled

| Path | Size | Why |
|---|---|---|
| `deps/platform-tools/adb` | ~5 MB | The Android Debug Bridge client `adb_tool.py` invokes. |
| `deps/platform-tools/fastboot` | ~1 MB | Bonus — needed by some BSP recovery flows. |
| `deps/platform-tools/*.so` etc. | ~25 MB | adb's runtime deps (USB, mDNS). |

## Resolution order in `adb_tool.py`

1. `$ADB_BIN` (explicit override)
2. `deps/platform-tools/adb` (this bundle — preferred)
3. `$(command -v adb)` (system fallback)
4. error + hint to run `fetch_deps.sh`

## Override the download

```bash
export ADB_PLATFORM_TOOLS_URL=https://internal-mirror/platform-tools-linux.zip
bash ~/.claude/skills/adb/deps/fetch_deps.sh
```
