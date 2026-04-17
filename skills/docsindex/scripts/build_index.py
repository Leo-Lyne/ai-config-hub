#!/usr/bin/env python3
# rag-mcp-server/build_index.py
"""CLI: Build and maintain enhanced tree indexes for PDF collections.

Usage:
    export GEMINI_API_KEY=your_key_here

    # First-time full build
    python build_index.py init --docs-path ./docs/bsp --index-dir ./index/bsp

    # Incremental update (add new, re-index modified, remove stale)
    python build_index.py update --docs-path ./docs/bsp --index-dir ./index/bsp

    # Use a specific model
    python build_index.py init --docs-path ./docs/bsp --index-dir ./index/bsp --model gpt-4o
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from tree import extract_tree, is_eda_bookmarks, collect_leaves
from enhance import enhance_tree, generate_tree_for_pdf
from store import save_tree, load_tree
from screen import screen_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def create_model(model_name: str):
    """Create an LLM model client based on model name.

    Supported models:
      - deepseek-chat: DeepSeek V3 (requires DEEPSEEK_API_KEY)
      - gpt-*: OpenAI models (requires OPENAI_API_KEY)
      - gemini-*: Google Gemini (requires GEMINI_API_KEY)
    """
    if model_name.startswith("deepseek"):
        import openai
        client = openai.AsyncOpenAI(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
            base_url="https://api.deepseek.com",
        )

        class DeepSeekAdapter:
            async def generate_content_async(self, prompt: str):
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                )
                class Result:
                    text = response.choices[0].message.content
                return Result()

        return DeepSeekAdapter()
    else:
        import openai
        client = openai.AsyncOpenAI()

        class OpenAIModelAdapter:
            async def generate_content_async(self, prompt: str):
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                )
                class Result:
                    text = response.choices[0].message.content
                return Result()

        return OpenAIModelAdapter()


def _save_partial(tree: dict, partial_path: Path):
    """Save checkpoint to a .partial.json file."""
    partial_path.write_text(json.dumps(tree, ensure_ascii=False, indent=2))


async def index_pdf(
    pdf_path: Path,
    index_dir: Path,
    model,
    skip_existing: bool = False,
) -> dict:
    """Index a single PDF: extract/generate tree, enhance descriptions, save.

    Uses .partial.json checkpoints so interrupted builds can resume without
    losing per-node progress.
    """
    pdf_name = pdf_path.name
    json_path = index_dir / f"{pdf_name}.json"
    partial_path = index_dir / f"{pdf_name}.partial.json"

    if skip_existing and json_path.exists():
        return {"status": "skipped", "pdf": pdf_name}

    # --- Check for partial checkpoint (resume) ---
    partial_tree = None
    if partial_path.exists():
        try:
            partial_tree = load_tree(partial_path)
            logger.info(f"  Resuming from checkpoint ({partial_path.name})")
        except Exception:
            partial_tree = None

    def checkpoint(t):
        _save_partial(t, partial_path)

    if partial_tree and partial_tree.get("_meta", {}).get("mode"):
        # Resume from checkpoint — screening and extraction already done
        meta = partial_tree["_meta"]
        decision = meta["mode"]
        strategy = meta.get("strategy", "enhance")
        tree = partial_tree
    else:
        # Fresh start — screen and extract
        decision, reason = await screen_pdf(pdf_path, model)

        tree = extract_tree(pdf_path)
        has_bookmarks = bool(tree["children"])
        is_eda = has_bookmarks and is_eda_bookmarks(tree)

        if decision == "browse_only":
            tree["_meta"] = {
                "pdf_path": str(pdf_path),
                "total_pages": tree["page_end"],
                "leaf_count": len(collect_leaves(tree)),
                "mode": "browse_only",
                "screen_reason": reason,
            }
            save_tree(tree, pdf_name, index_dir)
            return {"status": "browse_only", "pdf": pdf_name, "reason": reason}

        strategy = "generate" if (is_eda or not has_bookmarks) else "enhance"

        # Save initial checkpoint so screening result is preserved
        tree["_meta"] = {
            "pdf_path": str(pdf_path),
            "total_pages": tree["page_end"],
            "leaf_count": len(collect_leaves(tree)),
            "mode": "full",
            "strategy": strategy,
        }
        checkpoint(tree)

    # --- Full indexing with checkpoint ---
    if strategy == "generate":
        resume_page = tree.get("_meta", {}).get("generate_resume_page", 0)
        existing_children = tree.get("children", []) if resume_page > 0 else None
        tree = await generate_tree_for_pdf(
            pdf_path, model,
            resume_from_page=resume_page,
            existing_children=existing_children,
            checkpoint_callback=checkpoint,
        )
    else:
        leaves_before = len(collect_leaves(tree))
        if leaves_before > 0:
            tree = await enhance_tree(
                tree, pdf_path, model,
                checkpoint_callback=checkpoint,
            )

    tree["_meta"] = {
        "pdf_path": str(pdf_path),
        "total_pages": tree["page_end"],
        "leaf_count": len(collect_leaves(tree)),
        "mode": "full",
        "strategy": strategy,
    }
    save_tree(tree, pdf_name, index_dir)

    # Clean up partial checkpoint
    if partial_path.exists():
        partial_path.unlink()

    return {
        "status": "indexed",
        "pdf": pdf_name,
        "strategy": strategy,
        "pages": tree["page_end"],
        "leaves": len(collect_leaves(tree)),
    }


def scan_changes(docs_path: Path, index_dir: Path):
    """Compare PDFs in docs_path vs JSONs in index_dir.

    Returns:
        (to_add, to_remove, to_update): lists of Paths.
        - to_add: PDF paths with no corresponding .json
        - to_remove: .json paths whose source PDF no longer exists
        - to_update: PDF paths newer than their .json (modified)
    """
    pdfs = {p.name: p for p in sorted(docs_path.rglob("*.pdf"))}

    to_add: list[Path] = []
    to_remove: list[Path] = []
    to_update: list[Path] = []

    for pdf_name, pdf_path in pdfs.items():
        json_path = index_dir / f"{pdf_name}.json"
        if not json_path.exists():
            to_add.append(pdf_path)
        elif pdf_path.stat().st_mtime > json_path.stat().st_mtime:
            to_update.append(pdf_path)

    if index_dir.exists():
        for json_file in sorted(index_dir.glob("*.json")):
            if json_file.name.endswith(".partial.json"):
                continue
            # json_file.stem = "doc.pdf" for file "doc.pdf.json"
            pdf_name = json_file.stem
            if pdf_name not in pdfs:
                to_remove.append(json_file)

    return to_add, to_remove, to_update


async def _run_index_batch(pdfs: list[Path], index_dir: Path, model,
                           skip_existing: bool = False,
                           workers: int = 1) -> dict:
    """Index a list of PDFs, return aggregated results.

    Args:
        workers: Max number of PDFs to process concurrently.
            Each PDF may itself make multiple concurrent LLM calls internally.
    """
    results = {"indexed": 0, "skipped": 0, "browse_only": 0, "failed": 0}
    total = len(pdfs)
    counter = 0
    lock = asyncio.Lock()
    sem = asyncio.Semaphore(workers)

    async def _process_one(pdf_path: Path):
        nonlocal counter
        async with sem:
            async with lock:
                counter += 1
                n = counter
            logger.info(f"[{n}/{total}] {pdf_path.name}")
            try:
                result = await index_pdf(pdf_path, index_dir, model, skip_existing)
                async with lock:
                    results[result["status"]] = results.get(result["status"], 0) + 1
                if result["status"] == "indexed":
                    logger.info(
                        f"  -> {result['strategy']}: {result['pages']}p, "
                        f"{result['leaves']} leaves"
                    )
                elif result["status"] == "browse_only":
                    logger.info(f"  -> BROWSE_ONLY: {result['reason']}")
            except Exception as e:
                logger.error(f"  -> FAILED: {e}")
                async with lock:
                    results["failed"] += 1

    await asyncio.gather(*[_process_one(p) for p in pdfs])
    return results


async def cmd_init(args):
    """Full build: index all PDFs (skip already-completed ones)."""
    docs_path = Path(args.docs_path)
    index_dir = Path(args.index_dir)

    if not docs_path.exists():
        logger.error(f"Docs path does not exist: {docs_path}")
        sys.exit(1)

    pdfs = sorted(docs_path.rglob("*.pdf"))
    logger.info(f"Found {len(pdfs)} PDFs in {docs_path}")

    model = create_model(args.model)
    index_dir.mkdir(parents=True, exist_ok=True)

    workers = args.workers
    logger.info(f"Using {workers} concurrent workers")
    results = await _run_index_batch(pdfs, index_dir, model, skip_existing=True,
                                     workers=workers)

    logger.info(
        f"\nDone. Indexed: {results['indexed']}, "
        f"Skipped: {results['skipped']}, "
        f"Browse-only: {results['browse_only']}, Failed: {results['failed']}"
    )


async def cmd_update(args):
    """Incremental update: add new, re-index modified, remove stale."""
    docs_path = Path(args.docs_path)
    index_dir = Path(args.index_dir)

    if not docs_path.exists():
        logger.error(f"Docs path does not exist: {docs_path}")
        sys.exit(1)

    if not index_dir.exists():
        logger.error(f"Index dir does not exist: {index_dir} (run 'init' first)")
        sys.exit(1)

    to_add, to_remove, to_update = scan_changes(docs_path, index_dir)

    logger.info(
        f"Changes detected: {len(to_add)} new, "
        f"{len(to_update)} modified, {len(to_remove)} removed"
    )

    if not to_add and not to_remove and not to_update:
        logger.info("Index is up to date, nothing to do.")
        return

    # Remove stale indexes
    for json_path in to_remove:
        logger.info(f"  Removing stale index: {json_path.name}")
        json_path.unlink()
        # Also clean up any leftover partial
        partial = json_path.with_suffix(".partial.json")
        if partial.exists():
            partial.unlink()

    # Re-index modified PDFs (delete old json first)
    for pdf_path in to_update:
        json_path = index_dir / f"{pdf_path.name}.json"
        logger.info(f"  Re-indexing modified: {pdf_path.name}")
        json_path.unlink(missing_ok=True)

    # Index new + modified
    to_index = to_add + to_update
    if to_index:
        model = create_model(args.model)
        workers = args.workers
        logger.info(f"Using {workers} concurrent workers")
        results = await _run_index_batch(to_index, index_dir, model,
                                         workers=workers)
        logger.info(
            f"\nDone. Indexed: {results['indexed']}, "
            f"Browse-only: {results['browse_only']}, Failed: {results['failed']}"
        )

    logger.info(f"Removed: {len(to_remove)} stale indexes")


async def main():
    parser = argparse.ArgumentParser(
        description="Build and maintain enhanced tree indexes for PDF collections"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Shared arguments
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--docs-path", required=True, help="Directory containing PDFs")
    parent.add_argument("--index-dir", required=True, help="Output directory for index JSON files")
    parent.add_argument("--model", default="deepseek-chat", help="LLM model name")
    parent.add_argument("--workers", type=int, default=200,
                        help="Max concurrent PDFs to process (default: 200)")

    sub.add_parser("init", parents=[parent],
                    help="Full build (skip already-completed PDFs)")
    sub.add_parser("update", parents=[parent],
                    help="Incremental update (add new, re-index modified, remove stale)")

    args = parser.parse_args()

    if args.command == "init":
        await cmd_init(args)
    elif args.command == "update":
        await cmd_update(args)


if __name__ == "__main__":
    asyncio.run(main())
