"""
Shared primitives for androidbsp code-nav scripts.
Deployed by androidbsp-codeindex-setup to $BSP_ROOT/.codenav/scripts/.
All other scripts in the same dir import from here.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from packaging.version import Version

# ─── Version ───────────────────────────────────────────────────
BSP_COMMON_VERSION = Version("1.0.0")
SCHEMA_FINDING = "androidbsp.finding/v1"
SCHEMA_EVENT = "androidbsp.event/v1"

DEFAULT_TIMEOUT = 120
PARTITIONS = ["system", "vendor", "odm", "system_ext", "product"]
CODENAV_DIRNAME = ".codenav"


class BSPRootNotFound(RuntimeError):
    pass


# ─── Artifact discovery ────────────────────────────────────────
def find_bsp_root(start: Optional[Path] = None) -> Path:
    """Walk up from `start` looking for build/envsetup.sh. Default cwd."""
    cur = (start or Path.cwd()).resolve()
    while True:
        if (cur / "build" / "envsetup.sh").exists():
            return cur
        if cur.parent == cur:
            raise BSPRootNotFound(f"no build/envsetup.sh from {start}")
        cur = cur.parent


def load_active_files(bsp_root: Path) -> Optional[set[str]]:
    """Read .codenav/active_files.idx; return set of relative paths or None."""
    p = bsp_root / CODENAV_DIRNAME / "active_files.idx"
    if not p.exists():
        return None
    return {ln.strip() for ln in p.read_text().splitlines() if ln.strip()}


def parse_compile_commands(bsp_root: Path) -> list[dict]:
    """Read compile_commands.json at root. Return [] on failure with WARN."""
    p = bsp_root / "compile_commands.json"
    if not p.exists():
        print(f"WARN: no compile_commands.json at {bsp_root}", file=sys.stderr)
        return []
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARN: cannot parse compdb: {e}", file=sys.stderr)
        return []


# ─── Multi-partition / multi-candidate ────────────────────────
def first_existing(candidates: Iterable[Path]) -> Optional[Path]:
    """Return first existing path or None."""
    for p in candidates:
        if p.exists():
            return p
    return None


def scan_partitions(bsp_root: Path, subpath: str) -> list[Path]:
    """For each Android partition (system/vendor/odm/system_ext/product),
    return existing $bsp_root/<part>/<subpath> paths."""
    found = []
    for part in PARTITIONS:
        candidate = bsp_root / part / subpath
        if candidate.exists():
            found.append(candidate)
    return found


# ─── subprocess wrapper ───────────────────────────────────────
def run_cmd(cmd: list[str], *, timeout: int = DEFAULT_TIMEOUT,
            cwd: Optional[Path] = None,
            check: bool = False) -> subprocess.CompletedProcess:
    """Run cmd capturing stdout/stderr. Timeout returns non-zero, no exception."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd, check=check)
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(
            args=cmd, returncode=124,
            stdout=e.stdout.decode() if e.stdout else "",
            stderr=f"TIMEOUT after {timeout}s"
        )


# ─── Search wrappers ──────────────────────────────────────────
def rg_find(pattern: str, *, globs: Optional[list[str]] = None,
            root: Optional[Path] = None,
            extra: Optional[list[str]] = None,
            timeout: int = DEFAULT_TIMEOUT) -> list[tuple[str, int, str]]:
    """Run `rg -n --no-heading <pattern>`. Return [(file, line, snippet)]."""
    cmd = ["rg", "-n", "--no-heading"]
    for g in globs or []:
        cmd += ["-g", g]
    cmd += extra or []
    cmd.append(pattern)
    if root:
        cmd.append(str(root))
    r = run_cmd(cmd, timeout=timeout)
    if r.returncode not in (0, 1):  # 1 = no match (rg convention)
        print(f"WARN: rg exited {r.returncode}: {r.stderr.strip()}",
              file=sys.stderr)
        return []
    out = []
    for line in r.stdout.splitlines():
        # format: file:line:snippet
        parts = line.split(":", 2)
        if len(parts) == 3:
            try:
                out.append((parts[0], int(parts[1]), parts[2]))
            except ValueError:
                pass
    return out


def gtags_lookup(symbol: str, *, kind: str = "def",
                 root: Optional[Path] = None,
                 timeout: int = DEFAULT_TIMEOUT
                 ) -> list[tuple[str, int, str]]:
    """global wrapper. kind: def (-d), ref (-r), path (-P)."""
    flag = {"def": "-d", "ref": "-r", "path": "-P"}.get(kind, "-d")
    r = run_cmd(["global", flag, "-x", symbol], cwd=root, timeout=timeout)
    if r.returncode not in (0, 1):
        return []
    out = []
    for line in r.stdout.splitlines():
        # format: symbol lineno path snippet
        parts = line.split(None, 3)
        if len(parts) >= 4:
            try:
                out.append((parts[2], int(parts[1]), parts[3]))
            except ValueError:
                pass
    return out


# ─── Output structures ────────────────────────────────────────
@dataclass
class Finding:
    tag: str
    file: str
    line: int = 0
    snippet: str = ""
    info: dict = field(default_factory=dict)


def finding_to_dict(f: Finding) -> dict:
    d = asdict(f)
    d["schema"] = SCHEMA_FINDING
    return d


class Emitter:
    """Context manager: routes Findings to stdout (TSV or JSONL) and
    optionally appends Events to .codenav/events.jsonl.

    Usage:
        with Emitter(args, 'dt_bind.py') as e:
            e.emit(Finding(tag='DT', file='...', line=10, snippet='...'),
                   confidence='med', source='static-rg', tags=['dt'])
    """
    SCRIPT_VERSION = "1.0.0"  # bump per script if needed; default

    def __init__(self, args: argparse.Namespace, script_name: str):
        self.args = args
        self.script_name = script_name
        self.as_json = getattr(args, "json", False)
        self.no_events = getattr(args, "no_events", False)
        self.bsp_root = self._resolve_root(args)
        self._fp = None  # events.jsonl handle, lazy open

    def _resolve_root(self, args) -> Optional[Path]:
        try:
            return Path(getattr(args, "root", None) or find_bsp_root())
        except BSPRootNotFound:
            return None

    def __enter__(self):
        if not self.no_events and self.bsp_root:
            codenav = self.bsp_root / CODENAV_DIRNAME
            codenav.mkdir(exist_ok=True)
            self._fp = open(codenav / "events.jsonl", "a", buffering=1)
        return self

    def __exit__(self, *a):
        if self._fp:
            self._fp.close()

    def emit(self, finding: Finding, *, confidence: str = "med",
             source: str = "static-rg", tags: Optional[list[str]] = None
             ) -> None:
        # 1) stdout
        if self.as_json:
            sys.stdout.write(json.dumps(finding_to_dict(finding)) + "\n")
        else:
            info_str = " ".join(f"{k}={v}" for k, v in finding.info.items())
            sys.stdout.write(
                f"{finding.tag}\t{finding.file}:{finding.line}\t"
                f"{finding.snippet}\t{info_str}\n"
            )
        # 2) events.jsonl
        if self._fp:
            event = {
                "schema": SCHEMA_EVENT,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": source,
                "script": self.script_name,
                "script_version": self.SCRIPT_VERSION,
                "query": {
                    "args": sys.argv[1:],
                    "cwd": str(Path.cwd()),
                },
                "finding": finding_to_dict(finding),
                "confidence": confidence,
                "tags": tags or [],
            }
            self._fp.write(json.dumps(event, ensure_ascii=False) + "\n")


# ─── argparse helper ──────────────────────────────────────────
def make_parser(description: str) -> argparse.ArgumentParser:
    """ArgumentParser with --root, --json, --no-events, --timeout pre-injected."""
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--root", type=Path, default=None,
                   help="BSP root (default: auto-detect from cwd)")
    p.add_argument("--json", action="store_true",
                   help="emit JSONL on stdout instead of TSV")
    p.add_argument("--no-events", action="store_true",
                   help="do not append to .codenav/events.jsonl")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                   help=f"per-subprocess timeout in seconds (default {DEFAULT_TIMEOUT})")
    return p


# ─── Version compatibility ────────────────────────────────────
def require_version(min_version: str) -> None:
    if BSP_COMMON_VERSION < Version(min_version):
        raise RuntimeError(
            f"_bsp_common version {BSP_COMMON_VERSION} < required {min_version}"
        )
