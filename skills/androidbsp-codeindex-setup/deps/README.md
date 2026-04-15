# deps/ — Offline toolchain bundle

The skill requires: `ripgrep`, `fd-find`, `global` (gtags), `universal-ctags`,
`clangd`, `fzf`.

## Files

| File | Purpose |
|---|---|
| `install_tools.py` | **The one to run.** Installs missing tools. Auto-detects online/offline. |
| `fetch_deps.py`    | Run once on an online machine to populate `packages/` (for later offline use). |
| `packages/`        | `.deb` cache. Arch-specific; re-fetch on each target arch. |

## Normal use (online)

```
python3 install_tools.py
```

Installs missing tools via `apt-get install`. For already-installed tools,
reports available upgrades without applying them.

## Offline use

On an online machine with the matching Ubuntu/Debian release + arch:
```
python3 fetch_deps.py
```
Copy the whole skill directory to the target. On the target:
```
python3 install_tools.py --offline
```

## Flags

```
--offline     force offline install from packages/
--online      force online install, fail if no network
--check-only  just report current status, make no changes
```

## Notes

* `fd-find` installs the binary as `fdfind` on Debian/Ubuntu; the installer
  creates a `/usr/local/bin/fd` symlink automatically.
* `packages/` is **not** cross-arch — re-run `fetch_deps.py` on each arch
  (amd64 / arm64) you need to support.
