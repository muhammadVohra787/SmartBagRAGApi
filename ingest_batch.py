#!/usr/bin/env python3
"""
Batch ingestion script for PDF and Markdown files.

Usage:
  Local (uses .env.local):   python ingest_batch.py
  Dev (uses .env.dev):       RUN_MODE=dev python ingest_batch.py
  CI/CD:                     python ingest_batch.py --ci

Walks kb/pdf/ and kb/markdown/ directories, ingests all files.
Hash comparison ensures we only re-ingest changed files.

Environment:
  - Reads RUN_MODE env var (defaults to "local")
  - Loads .env.{RUN_MODE} file automatically via app.core.settings
  - Only needs Qdrant connection (QDRANT_URL, QDRANT_API_KEY)
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Set RUN_MODE before importing app modules (so settings loads correct .env file)
if "RUN_MODE" not in os.environ:
    os.environ["RUN_MODE"] = "local"

from app.models import IngestStatus
from app.pipelines.ingest_pdf_pipeline import ingest_pdf
from app.pipelines.ingest_markdown_pipeline import ingest_markdown
from app.stores.qdrant_store import ensure_collection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def collect_files(base_dir: Path) -> tuple[list[Path], list[Path]]:
    """Return (pdf_files, markdown_files) from kb/ directory."""
    pdf_dir = base_dir / "pdf"
    md_dir = base_dir / "markdown"

    pdfs = list(pdf_dir.rglob("*.pdf")) if pdf_dir.exists() else []
    mds = list(md_dir.rglob("*.md")) if md_dir.exists() else []

    return pdfs, mds


def main():
    parser = argparse.ArgumentParser(description="Batch ingest PDFs and Markdown files")
    parser.add_argument(
        "--kb-dir",
        type=Path,
        default=Path("kb"),
        help="Knowledge base directory (default: ./kb)",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: exit with code 1 if any ingestion fails",
    )
    args = parser.parse_args()

    kb_dir: Path = args.kb_dir
    if not kb_dir.exists():
        log.error("KB directory not found: %s", kb_dir)
        sys.exit(1)

    # Ensure Qdrant collection and indexes exist
    log.info("Ensuring Qdrant collection exists...")
    try:
        ensure_collection()
    except Exception as exc:
        log.error("Failed to ensure Qdrant collection: %s", exc)
        sys.exit(1)

    # Collect files
    pdfs, mds = collect_files(kb_dir)
    log.info("Found %d PDFs and %d Markdown files", len(pdfs), len(mds))

    if not pdfs and not mds:
        log.warning("No files to ingest. Exiting.")
        sys.exit(0)

    # Track results
    results = {
        "ingested": 0,
        "skipped": 0,
        "errors": 0,
    }
    error_files: list[str] = []

    # Ingest PDFs
    for pdf_path in pdfs:
        log.info("Processing PDF: %s", pdf_path)
        try:
            result = ingest_pdf(str(pdf_path))
            if result.status == IngestStatus.ingested:
                results["ingested"] += 1
            elif result.status == IngestStatus.skipped:
                results["skipped"] += 1
            elif result.status == IngestStatus.error:
                results["errors"] += 1
                error_files.append(str(pdf_path))
                log.error("Failed to ingest %s: %s", pdf_path, result.message)
        except Exception as exc:
            results["errors"] += 1
            error_files.append(str(pdf_path))
            log.exception("Unhandled exception ingesting %s: %s", pdf_path, exc)

    # Ingest Markdown
    for md_path in mds:
        log.info("Processing Markdown: %s", md_path)
        try:
            result = ingest_markdown(str(md_path))
            if result.status == IngestStatus.ingested:
                results["ingested"] += 1
            elif result.status == IngestStatus.skipped:
                results["skipped"] += 1
            elif result.status == IngestStatus.error:
                results["errors"] += 1
                error_files.append(str(md_path))
                log.error("Failed to ingest %s: %s", md_path, result.message)
        except Exception as exc:
            results["errors"] += 1
            error_files.append(str(md_path))
            log.exception("Unhandled exception ingesting %s: %s", md_path, exc)

    # Summary
    log.info("=" * 60)
    log.info("Batch ingestion complete")
    log.info("  Ingested: %d files", results["ingested"])
    log.info("  Skipped:  %d files (unchanged)", results["skipped"])
    log.info("  Errors:   %d files", results["errors"])

    if error_files:
        log.error("Failed files:")
        for f in error_files:
            log.error("  - %s", f)

    # CI mode: fail if any errors
    if args.ci and results["errors"] > 0:
        log.error("CI mode: exiting with code 1 due to errors")
        sys.exit(1)

    log.info("Done.")


if __name__ == "__main__":
    main()
