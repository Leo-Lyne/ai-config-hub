# rag-mcp-server/screen.py
"""PDF pre-screening via two-stage LLM evaluation.

Stage 1 (lightweight): filename + ~1000 chars from random discrete pages → quick verdict
Stage 2 (structural):  filename + first 3 pages + TOC chapter starts + last 3 pages → full verdict

No hardcoded whitelists or vendor lists. The LLM decides purely from content.
"""
from __future__ import annotations

import json
import logging
import random
import re
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage 1: Lightweight prompt — filename + ~1000 chars random discrete sample
# ---------------------------------------------------------------------------
STAGE1_PROMPT = """\
You are a document classifier for a RAG indexing system.

Given the filename and a small random text sample from a PDF, decide:
Is this document published by the organization that DEFINES a standard or architecture, \
or by a product vendor that IMPLEMENTS it?

**Filename**: "{filename}"
**Total pages**: {total_pages}
**Random text sample** (~1000 chars from {num_segments} discrete locations):
---
{sample_text}
---

Classification — who published this document?

1. "well_known" = true: Published by the body that DEFINES the standard/architecture. \
These documents ARE the authoritative source — LLMs have them in training data regardless \
of how detailed, version-specific, or register-level they are.
   - Standards bodies: IEEE, MIPI Alliance, USB-IF, JEDEC, PCI-SIG, VESA, ISO, IETF (RFCs)
   - Architecture owners: ARM (Cortex-A guides, GIC specs, ARM ARM — all versions), \
Intel (x86 manuals), RISC-V Foundation
   - Kernel/framework maintainers: official Linux kernel docs, Buildroot manual, U-Boot docs
   - Any version of these (v1, v2, v3...) is still the standard itself

2. "well_known" = false: Published by a PRODUCT VENDOR that builds on those standards. \
Even if the topic is well-known, the vendor's specific implementation details are NOT \
in LLM training data.
   - SoC vendors: Rockchip, Qualcomm, MediaTek, NXP, Samsung, Allwinner, Amlogic...
   - Board vendors: 正点原子, Firefly, Toybrick, Khadas, Radxa, Pine64...
   - IC datasheets: specific chip register maps, electrical specs (ILI9881C, HX8399...)
   - Any vendor's "developer guide", "BSP guide", "hardware design guide", tutorial

Return ONLY: {{"well_known": true|false, "reason": "one-sentence reason"}}
"""


# ---------------------------------------------------------------------------
# Stage 2: Structural prompt — first pages + TOC chapters + last pages
# ---------------------------------------------------------------------------
STAGE2_PROMPT = """\
You are evaluating whether a PDF document needs full LLM-enhanced indexing, \
or if bookmark-only indexing is sufficient.

**Document**: "{filename}" ({total_pages} pages)
**Sampled content** (first pages, chapter starts from TOC, last pages):
---
{pages_text}
---

The core question: Is this document published by the organization that DEFINES \
a standard/architecture, or by a product vendor that IMPLEMENTS it?

- "browse_only": Published by the DEFINING body — the document IS the standard itself. \
LLMs already know the content from training data. Bookmark-only indexing is sufficient.
  Examples: IEEE specs (any version), MIPI Alliance specs (DSI, CSI, D-PHY — any version), \
ARM official docs (ARM ARM, Cortex-A/R/M programmer's guides, GIC specs — any version), \
USB-IF specs, JEDEC standards, PCI-SIG specs, RFCs, official open-source project docs.
  NOTE: Do NOT reject these just because they contain "detailed register maps" or \
"version-specific content" — that IS the standard. ARM's register descriptions, MIPI's \
timing parameters, USB's descriptor formats are all public knowledge in LLM training data.

- "full": Published by a PRODUCT VENDOR — contains implementation-specific content that \
LLMs cannot know. This includes: SoC vendor docs (Rockchip, Qualcomm, MediaTek...), \
board vendor tutorials (正点原子, Firefly...), specific IC datasheets (ILI9881C, HX8399...), \
any vendor's developer guide / BSP guide / hardware design guide.

Return ONLY: {{"decision": "full"|"browse_only", "reason": "one-sentence reason in the document's language"}}
"""


# ---------------------------------------------------------------------------
# Sampling functions
# ---------------------------------------------------------------------------

def _sample_light(pdf_path: Path, target_chars: int = 1000, num_segments: int = 10) -> tuple[str, int, int]:
    """Lightweight sampling: randomly pick pages and extract text segments.

    Distributes samples across the full document to get a representative picture.
    Returns: (sample_text, total_pages, actual_segments_used)
    """
    doc = fitz.open(str(pdf_path))
    total = len(doc)

    if total == 0:
        doc.close()
        return "", 0, 0

    # Pick random pages spread across the document
    page_indices = random.sample(range(total), min(num_segments, total))

    segments = []
    chars_collected = 0
    chars_per_segment = target_chars // num_segments

    for page_num in sorted(page_indices):
        if chars_collected >= target_chars:
            break
        text = doc[page_num].get_text().strip()
        if not text:
            continue
        # Take a chunk from each page (not always from the start — vary position)
        max_start = max(0, len(text) - chars_per_segment)
        start = random.randint(0, max_start) if max_start > 0 else 0
        segment = text[start:start + chars_per_segment].strip()
        if segment:
            segments.append(f"[p{page_num + 1}] {segment}")
            chars_collected += len(segment)

    doc.close()
    return "\n...\n".join(segments), total, len(segments)


def _sample_pages(pdf_path: Path, max_chars_per_page: int = 1500) -> tuple[str, int]:
    """Structure-aware sampling: first 3 pages + TOC chapter starts + last 3 pages."""
    doc = fitz.open(str(pdf_path))
    total = len(doc)

    indices: set[int] = set()

    # Always take first 3 pages (title, abstract, TOC, intro)
    for i in range(min(3, total)):
        indices.add(i)

    # Extract chapter start pages from PDF bookmarks/TOC
    try:
        toc = doc.get_toc()
        if toc:
            for level, _, page_num in toc:
                if level == 1:
                    page_idx = max(0, page_num - 1)
                    if 3 <= page_idx < total - 3:
                        indices.add(page_idx)
    except Exception:
        pass

    # Always take last 3 pages (appendix, references, index)
    for i in range(max(0, total - 3), total):
        indices.add(i)

    parts = []
    for i in sorted(indices):
        text = doc[i].get_text().strip()[:max_chars_per_page]
        if text:
            parts.append(f"=== Page {i + 1}/{total} ===\n{text}")

    doc.close()
    return "\n\n".join(parts), total


# ---------------------------------------------------------------------------
# Main screening function
# ---------------------------------------------------------------------------

async def screen_pdf(pdf_path: Path, model) -> tuple[str, str]:
    """Two-stage LLM screening. No hardcoded rules — the LLM decides from content.

    Stage 1: Random ~1000 chars + filename → quick verdict (1 LLM call, cheap)
             If well_known=true → return browse_only immediately
    Stage 2: Structural sample + filename → full verdict (1 LLM call, thorough)
             Only reached if Stage 1 says well_known=false

    Returns:
        ("full", "") if document needs full LLM-enhanced indexing.
        ("browse_only", reason) if bookmark-only indexing is sufficient.
    """
    # Stage 1: Lightweight screening
    light_sample, total_pages, num_segments = _sample_light(pdf_path)

    if not light_sample.strip():
        return "full", ""

    stage1_prompt = STAGE1_PROMPT.format(
        filename=pdf_path.name,
        total_pages=total_pages,
        num_segments=num_segments,
        sample_text=light_sample,
    )

    try:
        response = await model.generate_content_async(stage1_prompt)
        text = response.text.strip()
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)

        result = json.loads(text)
        is_known = result.get("well_known", False)
        reason = result.get("reason", "")

        if is_known:
            msg = f"[S1→browse_only] {pdf_path.name} ({reason})"
            print(msg, flush=True)
            logger.info(msg)
            return "browse_only", f"Stage 1: {reason}"

        logger.info(f"[S1→stage2] {pdf_path.name} ({reason})")
    except Exception as e:
        logger.warning(f"[S1→error] {pdf_path.name}: {e}, falling through to stage 2")

    # Stage 2: Structure-aware screening
    pages_text, total_pages = _sample_pages(pdf_path)

    if not pages_text.strip():
        return "full", ""

    stage2_prompt = STAGE2_PROMPT.format(
        filename=pdf_path.name,
        total_pages=total_pages,
        pages_text=pages_text,
    )

    try:
        response = await model.generate_content_async(stage2_prompt)
        text = response.text.strip()
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)

        result = json.loads(text)
        decision = result.get("decision", "full")
        reason = result.get("reason", "")

        msg = f"[S2→{decision}] {pdf_path.name} ({reason})"
        print(msg, flush=True)
        logger.info(msg)

        return decision, reason
    except Exception as e:
        logger.warning(f"[S2→error] {pdf_path.name}: {e}, defaulting to full")
        return "full", ""
