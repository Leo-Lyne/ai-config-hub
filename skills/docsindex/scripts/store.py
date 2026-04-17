# rag-mcp-server/store.py
"""Save and load enhanced tree indexes as JSON files."""
from __future__ import annotations

import json
from pathlib import Path

from tree import collect_leaves


def save_tree(tree: dict, pdf_name: str, index_dir: str | Path) -> Path:
    """Save an enhanced tree to index_dir/<pdf_name>.json.

    Adds _meta block with pdf_path, total_pages, leaf_count.
    """
    index_dir = Path(index_dir)
    index_dir.mkdir(parents=True, exist_ok=True)

    existing_meta = tree.get("_meta", {})
    existing_meta.update({
        "pdf_path": existing_meta.get("pdf_path", ""),
        "total_pages": tree.get("page_end", 0),
        "leaf_count": len(collect_leaves(tree)),
    })
    tree["_meta"] = existing_meta

    out_path = index_dir / f"{pdf_name}.json"
    out_path.write_text(json.dumps(tree, ensure_ascii=False, indent=2))
    return out_path


def load_tree(json_path: str | Path) -> dict:
    """Load a tree from a JSON file."""
    return json.loads(Path(json_path).read_text())


def build_manifest(index_dir: str | Path) -> list[dict]:
    """Build a manifest listing all indexed documents.

    Returns list of {doc_id, title, total_pages, leaf_count, pdf_path}.
    """
    index_dir = Path(index_dir)
    manifest = []

    for jp in sorted(index_dir.glob("*.json")):
        try:
            tree = json.loads(jp.read_text())
            meta = tree.get("_meta", {})
            manifest.append({
                "doc_id": jp.stem,  # "some_doc.pdf"
                "title": tree.get("title", jp.stem),
                "total_pages": meta.get("total_pages", tree.get("page_end", 0)),
                "leaf_count": meta.get("leaf_count", 0),
                "pdf_path": meta.get("pdf_path", ""),
            })
        except (json.JSONDecodeError, KeyError):
            continue

    return manifest
