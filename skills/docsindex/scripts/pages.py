# rag-mcp-server/pages.py
"""Read page text and images from PDF files."""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


def read_page_text(pdf_path: str | Path, start: int, end: int) -> str:
    """Extract text from page range [start, end] (1-based, inclusive).

    Out-of-range pages are clamped silently.
    """
    doc = fitz.open(str(pdf_path))
    texts = []
    for i in range(max(0, start - 1), min(end, len(doc))):
        text = doc[i].get_text()
        texts.append(f"--- Page {i + 1} ---\n{text}")
    doc.close()
    return "\n".join(texts)


def read_page_image(pdf_path: str | Path, page_num: int, dpi: int = 150) -> bytes:
    """Render a single page as PNG bytes (1-based page number)."""
    doc = fitz.open(str(pdf_path))
    page = doc[page_num - 1]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def sample_section_text(
    pdf_path: str | Path, start: int, end: int, max_chars: int = 3000
) -> str:
    """Read text from a page range, truncated to max_chars.

    Prioritizes the first page and trims from there.
    Used by enhance.py to provide context for LLM description generation.
    """
    doc = fitz.open(str(pdf_path))
    texts = []
    total = 0
    for i in range(max(0, start - 1), min(end, len(doc))):
        page_text = doc[i].get_text().strip()
        header = f"--- Page {i + 1} ---\n"
        if total + len(header) + len(page_text) > max_chars:
            remaining = max_chars - total - len(header)
            if remaining > 50:
                texts.append(header + page_text[:remaining] + "...")
            break
        texts.append(header + page_text)
        total += len(header) + len(page_text)
    doc.close()
    return "\n".join(texts)
