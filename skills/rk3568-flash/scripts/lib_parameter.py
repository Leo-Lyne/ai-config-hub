"""Parser for Rockchip parameter.txt (GPT layout descriptor)."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

EXPECTED_MAGIC = 0x5041524B
_PARTITION_RE = re.compile(r"\(([^:)]+)(?::grow)?\)")


@dataclass
class Parameter:
    raw: str
    machine_model: str = ""
    firmware_ver: str = ""
    magic: int = 0
    cmdline: str = ""
    partitions: List[str] = field(default_factory=list)

    def is_magic_valid(self) -> bool:
        return self.magic == EXPECTED_MAGIC


def parse(path: Path | str) -> Parameter:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    p = Parameter(raw=raw)
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("MACHINE_MODEL:"):
            p.machine_model = line.split(":", 1)[1].strip()
        elif line.startswith("FIRMWARE_VER:"):
            p.firmware_ver = line.split(":", 1)[1].strip()
        elif line.startswith("MAGIC:"):
            tok = line.split(":", 1)[1].strip()
            try:
                p.magic = int(tok, 16) if tok.lower().startswith("0x") else int(tok)
            except ValueError:
                p.magic = 0
        elif line.startswith("CMDLINE:"):
            p.cmdline = line.split(":", 1)[1].strip()
    p.partitions = _PARTITION_RE.findall(p.cmdline)
    return p
