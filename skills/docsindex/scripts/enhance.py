# rag-mcp-server/enhance.py
"""LLM-based tree enhancement and generation using Gemini Flash."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from pages import sample_section_text
from tree import collect_leaves

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_enhance_prompt(nodes: list[dict], texts: list[str]) -> str:
    """Build a prompt to enhance descriptions for a batch of leaf nodes."""
    sections = []
    for i, (node, text) in enumerate(zip(nodes, texts)):
        sections.append(
            f'Section {i}: "{node["title"]}" (pages {node["page"]}-{node["page_end"]})\n'
            f"Text:\n{text}\n"
        )

    return (
        "You are analyzing sections from a technical PDF document.\n"
        "For each section below, write a concise description (1-3 sentences) that captures:\n"
        "- What the section covers\n"
        "- Key technical terms, register names, base addresses, protocol names\n"
        "- Type of content (register map, timing diagram, code example, block diagram, etc.)\n\n"
        "The descriptions will be used as search metadata — be specific enough to "
        "distinguish this section from similar ones in the same document.\n\n"
        + "\n".join(sections)
        + "\nReturn a JSON array. Each element: {\"index\": <int>, \"description\": \"<text>\"}.\n"
        "Return ONLY the JSON array, no markdown fences or extra text."
    )


def _build_generate_prompt(pages_text: str, total_pages: int) -> str:
    """Build a prompt to generate a tree structure for a PDF without bookmarks."""
    return (
        f"You are analyzing a technical PDF document ({total_pages} pages total).\n"
        "Based on the page content below, generate a hierarchical table of contents.\n\n"
        "For each section, provide:\n"
        "- title: descriptive section title\n"
        "- page: starting page number (1-based)\n"
        "- page_end: ending page number (1-based, inclusive)\n"
        "- description: 1-2 sentence description of what the section covers\n"
        "- children: nested sub-sections (same format), or empty array\n\n"
        "Rules:\n"
        "- Every page must belong to at least one section\n"
        "- Sections should reflect the document's logical structure\n"
        "- Use descriptive titles, not generic ones like 'Section 1'\n\n"
        f"{pages_text}\n\n"
        "Return a JSON array of top-level sections. "
        "Return ONLY the JSON array, no markdown fences or extra text."
    )


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------

def _parse_enhance_response(text: str, expected_count: int) -> list[str]:
    """Parse LLM response for description enhancement. Returns list of descriptions."""
    text = text.strip()
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)

    try:
        items = json.loads(text)
        if not isinstance(items, list):
            return [""] * expected_count
        descriptions = [""] * expected_count
        for item in items:
            idx = item.get("index", -1)
            if 0 <= idx < expected_count:
                descriptions[idx] = item.get("description", "")
        return descriptions
    except (json.JSONDecodeError, TypeError, KeyError):
        logger.warning("Failed to parse enhance response, returning empty descriptions")
        return [""] * expected_count


def _parse_generate_response(text: str) -> list[dict]:
    """Parse LLM response for tree generation. Returns list of section dicts."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)

    try:
        sections = json.loads(text)
        if not isinstance(sections, list):
            return []
        return _normalize_sections(sections)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Failed to parse generate response")
        return []


def _normalize_sections(sections: list[dict]) -> list[dict]:
    """Ensure each section dict has the required fields."""
    result = []
    for s in sections:
        node = {
            "title": s.get("title", "Untitled"),
            "page": s.get("page", 1),
            "page_end": s.get("page_end", s.get("page", 1)),
            "description": s.get("description", ""),
            "children": [],
        }
        if s.get("children"):
            node["children"] = _normalize_sections(s["children"])
        result.append(node)
    return result


# ---------------------------------------------------------------------------
# Core enhancement functions
# ---------------------------------------------------------------------------

async def enhance_tree(
    tree: dict,
    pdf_path: str | Path,
    model,
    batch_size: int = 5,
    max_concurrent: int = 5,
    progress_callback=None,
    checkpoint_callback=None,
) -> dict:
    """Enhance all leaf node descriptions in a tree via LLM.

    Args:
        tree: Tree dict from tree.extract_tree().
        pdf_path: Path to the source PDF.
        model: google.generativeai.GenerativeModel instance (or mock).
        batch_size: Number of leaves per LLM call.
        max_concurrent: Max parallel API calls.
        progress_callback: Optional callable(done, total) for progress tracking.
        checkpoint_callback: Optional callable(tree) called after each batch
            completes, allowing the caller to persist intermediate state.
    """
    import copy
    tree = copy.deepcopy(tree)
    all_leaves = collect_leaves(tree)

    if not all_leaves:
        return tree

    # Skip leaves that already have descriptions (checkpoint resume)
    pending = [l for l in all_leaves if not l.get("description")]
    if not pending:
        return tree

    total = len(all_leaves)
    done_count = total - len(pending)

    sem = asyncio.Semaphore(max_concurrent)
    _checkpoint_lock = asyncio.Lock()

    async def process_batch(batch_leaves: list[dict]):
        nonlocal done_count
        async with sem:
            texts = [
                sample_section_text(pdf_path, n["page"], n["page_end"], max_chars=2000)
                for n in batch_leaves
            ]
            prompt = _build_enhance_prompt(batch_leaves, texts)

            try:
                response = await model.generate_content_async(prompt)
                descriptions = _parse_enhance_response(response.text, len(batch_leaves))
            except Exception as e:
                logger.warning(f"LLM call failed: {e}")
                descriptions = [""] * len(batch_leaves)

            for node, desc in zip(batch_leaves, descriptions):
                if desc:
                    node["description"] = desc

            done_count += len(batch_leaves)
            if progress_callback:
                progress_callback(done_count, total)
            if checkpoint_callback:
                async with _checkpoint_lock:
                    checkpoint_callback(tree)

    # Create batches from pending leaves only
    batches = [pending[i : i + batch_size] for i in range(0, len(pending), batch_size)]
    await asyncio.gather(*[process_batch(b) for b in batches])

    return tree


async def generate_tree_for_pdf(
    pdf_path: str | Path,
    model,
    pages_per_batch: int = 10,
    resume_from_page: int = 0,
    existing_children: list[dict] | None = None,
    checkpoint_callback=None,
) -> dict:
    """Generate a tree structure for a PDF that has no bookmarks.

    Sends page content to LLM in batches and merges results.

    Args:
        resume_from_page: 0-based page index to resume from (skip earlier batches).
        existing_children: Previously generated sections to prepend (for resume).
        checkpoint_callback: Optional callable(tree) called after each batch.
    """
    import fitz

    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)

    all_sections: list[dict] = list(existing_children) if existing_children else []

    for start in range(resume_from_page, total_pages, pages_per_batch):
        end = min(start + pages_per_batch, total_pages)
        pages_text = ""
        for i in range(start, end):
            page_text = doc[i].get_text().strip()[:1500]
            pages_text += f"=== Page {i + 1} ===\n{page_text}\n\n"

        prompt = _build_generate_prompt(pages_text, total_pages)

        try:
            response = await model.generate_content_async(prompt)
            sections = _parse_generate_response(response.text)
            all_sections.extend(sections)
        except Exception as e:
            logger.warning(f"Tree generation failed for pages {start+1}-{end}: {e}")

        tree = {
            "title": pdf_path.stem,
            "page": 1,
            "page_end": total_pages,
            "children": all_sections,
            "description": "",
            "_meta": {"generate_resume_page": end},
        }
        if checkpoint_callback:
            checkpoint_callback(tree)

    doc.close()

    return {
        "title": pdf_path.stem,
        "page": 1,
        "page_end": total_pages,
        "children": all_sections,
        "description": "",
    }
