# rag-mcp-server/tree.py
"""Extract PDF bookmark trees and provide navigation utilities."""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


def extract_tree(pdf_path: str | Path) -> dict:
    """Extract PDF bookmarks into a nested tree structure.

    Returns tree dict with: title, page (1-based), page_end, children, description.
    If the PDF has no bookmarks, returns a root node with empty children.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    toc = doc.get_toc()  # [[level, title, page], ...]
    total_pages = len(doc)
    doc.close()

    root = {
        "title": pdf_path.stem,
        "page": 1,
        "page_end": total_pages,
        "children": [],
        "description": "",
    }

    if not toc:
        return root

    stack: list[tuple[int, dict]] = [(0, root)]

    for level, title, page in toc:
        title = title.strip()
        # Skip junk bookmark entries (bare numbers like "1", "1.1", "1.6.1")
        if not title or all(c.isdigit() or c == "." for c in title):
            continue

        node = {
            "title": title,
            "page": page,
            "page_end": None,
            "children": [],
            "description": "",
        }

        # Pop stack until we find the parent level
        while stack and stack[-1][0] >= level:
            stack.pop()

        parent = stack[-1][1] if stack else root
        parent["children"].append(node)
        stack.append((level, node))

    _infer_page_ends(root, total_pages)
    return root


def _infer_page_ends(node: dict, doc_total: int):
    """Fill page_end by looking at next sibling's start page."""
    children = node.get("children", [])
    for i, child in enumerate(children):
        if i + 1 < len(children):
            next_start = children[i + 1]["page"]
            child["page_end"] = max(child["page"], next_start - 1)
        else:
            child["page_end"] = max(child["page"], node.get("page_end", doc_total))
        _infer_page_ends(child, doc_total)


def is_eda_bookmarks(tree: dict) -> bool:
    """Detect EDA-style bookmarks (schematic PDFs): many leaves, few pages."""
    leaves = collect_leaves(tree)
    total_pages = tree.get("page_end", 1) - tree.get("page", 1) + 1
    if total_pages < 1:
        total_pages = 1
    return len(leaves) > 500 and len(leaves) / total_pages > 20


def tree_to_compact(node: dict, max_depth: int = 99, _depth: int = 0) -> str:
    """Render tree as indented text for LLM context.

    Format: [p42-67] Chapter 5 USB Controller
    If node has a description, appends: -- description text
    """
    lines = []
    indent = "  " * _depth
    p_start = node["page"]
    p_end = node.get("page_end", p_start)
    page_span = f"p{p_start}" if p_start == p_end else f"p{p_start}-{p_end}"

    desc = node.get("description", "")
    desc_suffix = f" -- {desc}" if desc else ""
    lines.append(f"{indent}[{page_span}] {node['title']}{desc_suffix}")

    if _depth < max_depth:
        for child in node.get("children", []):
            lines.append(tree_to_compact(child, max_depth, _depth + 1))

    return "\n".join(lines)


def find_nodes(node: dict, keywords: list[str], _path: str = "") -> list[dict]:
    """Find all nodes whose title OR description matches ALL keywords (case-insensitive)."""
    results = []
    current_path = f"{_path} > {node['title']}" if _path else node["title"]
    searchable = (node["title"] + " " + node.get("description", "")).lower()

    if all(kw.lower() in searchable for kw in keywords):
        results.append({
            "path": current_path,
            "title": node["title"],
            "description": node.get("description", ""),
            "page": node["page"],
            "page_end": node.get("page_end"),
        })

    for child in node.get("children", []):
        results.extend(find_nodes(child, keywords, current_path))

    return results


def collect_leaves(node: dict) -> list[dict]:
    """Collect all leaf nodes (nodes with no children)."""
    if not node.get("children"):
        return [node]
    leaves = []
    for child in node["children"]:
        leaves.extend(collect_leaves(child))
    return leaves


def get_subtree(node: dict, keyword: str) -> dict | None:
    """Return the first subtree whose title contains keyword (case-insensitive)."""
    if keyword.lower() in node["title"].lower():
        return node
    for child in node.get("children", []):
        result = get_subtree(child, keyword)
        if result:
            return result
    return None
