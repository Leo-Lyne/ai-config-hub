#!/usr/bin/env python3
"""Convert PDFs to Markdown using Marker (ML-based layout + image extraction).

Each PDF <name>.pdf becomes sibling outputs next to the source:
  <name>.md                  -- markdown with image refs
  <name>_images/*.{png,jpg}  -- extracted figures/schematics

Invoked via the pdf2md skill's bundled venv:
  ~/.claude/skills/pdf2md/.venv/bin/python \\
    ~/.claude/skills/pdf2md/scripts/pdf2md.py [targets...]

Without args, recursively converts every *.pdf under CWD.
With args, only converts the given paths.
Existing .md outputs are skipped (idempotent).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


# Quiet down tokenizer/hf noise before importing marker.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


# ── Bundled-venv bootstrap ────────────────────────────────────────
# Try ambient `import marker`; if it fails, re-exec under deps/.venv/bin/python
# (created by deps/install.sh). Final fallback: print install hint and exit.
def _ensure_marker():
    try:
        import marker  # noqa: F401
        return
    except ImportError:
        pass
    skill_root = Path(__file__).resolve().parent.parent
    venv_dir = skill_root / "deps" / ".venv"
    venv_py = venv_dir / "bin" / "python"
    # Loop-guard: refuse to execv into the same venv twice (venv's import also failed).
    already_in_venv = sys.prefix == str(venv_dir) or os.environ.get("_PDF2MD_BOOTSTRAPPED") == "1"
    if venv_py.is_file() and not already_in_venv:
        os.environ["_PDF2MD_BOOTSTRAPPED"] = "1"
        os.execv(str(venv_py), [str(venv_py), __file__, *sys.argv[1:]])
    print(
        "[ERROR] marker-pdf not importable. Build the bundled venv:\n"
        "        bash $HOME/.claude/skills/pdf2md/deps/install.sh",
        file=sys.stderr,
    )
    sys.exit(1)


_ensure_marker()


def _load_converter():
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    return PdfConverter(artifact_dict=create_model_dict())


def convert_pdf(pdf_path: Path, converter) -> Path:
    from marker.output import text_from_rendered

    out_dir = pdf_path.parent
    md_path = out_dir / (pdf_path.stem + ".md")
    img_dir = out_dir / (pdf_path.stem + "_images")

    if md_path.exists():
        print(f"  skip (exists): {md_path}")
        return md_path

    print(f"  converting: {pdf_path.name}")
    rendered = converter(str(pdf_path))
    md_text, _, images = text_from_rendered(rendered)

    if images:
        img_dir.mkdir(exist_ok=True)
        for name, img in images.items():
            img.save(img_dir / name)
            md_text = md_text.replace(f"]({name})", f"]({img_dir.name}/{name})")

    md_path.write_text(md_text, encoding="utf-8")
    print(f"  -> {md_path} ({len(images)} images)")
    return md_path


def main() -> int:
    if len(sys.argv) > 1:
        targets = [Path(p).resolve() for p in sys.argv[1:]]
    else:
        # Scan CWD so users can `cd` into any PDF folder and invoke the skill.
        targets = sorted(Path.cwd().rglob("*.pdf"))
        targets = [p for p in targets if ".venv" not in p.parts]

    print(f"Found {len(targets)} PDF(s)")
    if not targets:
        return 0

    print("Loading Marker models (first run will download ~2GB)...")
    converter = _load_converter()

    failures = 0
    for pdf in targets:
        try:
            convert_pdf(pdf, converter)
        except Exception as e:
            failures += 1
            print(f"  ERROR {pdf.name}: {e}")

    print(f"Done. {len(targets) - failures}/{len(targets)} succeeded.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
