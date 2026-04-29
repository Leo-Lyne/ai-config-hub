# usbip / deps

Self-contained installer for `usbipd-win` — the Windows host-side service that
the `/usbip` skill relies on for USB pass-through into WSL2.

## Bootstrap (WSL2)

```bash
bash ~/.claude/skills/usbip/deps/fetch_deps.sh        # download MSI (~3 MB)
bash ~/.claude/skills/usbip/deps/install_windows.sh   # silent install via UAC
```

Open a fresh WSL terminal afterwards so Windows PATH inheritance picks up
`usbipd.exe`. Then `/usbip list` should work.

## What gets bundled

| Path | Size | Why |
|---|---|---|
| `deps/packages/usbipd-win_*.msi` | ~3 MB | The Windows-side daemon. Required to bind/attach USB devices. |

The Linux-side `usbip_tool.py` only `subprocess`-calls `usbipd.exe` and parses
its output — no Python deps needed.

## Manual install (if scripts won't run)

From any Windows shell (admin):

```powershell
msiexec /i "<path-to>\usbipd-win_*.msi" /passive
```

Or via `winget`:

```powershell
winget install usbipd
```

Either way: open a fresh WSL terminal, then `usbipd.exe --version` should work.

## Override the download

```bash
export USBIPD_VER=5.0.0
export USBIPD_MSI_URL=https://internal-mirror/usbipd-win_5.0.0.msi
bash ~/.claude/skills/usbip/deps/fetch_deps.sh
```
