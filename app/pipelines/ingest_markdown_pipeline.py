"""
ingestion/markdown.py

Markdown ingestion pipeline.

Entry point
-----------
    ingest_markdown(file_path: str) -> BatchIngestionResult

What it does
------------
1.  Compute SHA-256 of file contents.
2.  Check Qdrant — if stored hash matches, skip the file.
3.  Parse with markdown-it-py to extract heading boundaries.
4.  Split on headings first (MarkdownHeaderTextSplitter) — each section
    carries its heading text at the top so the heading is always embedded
    with the content below it.
5.  Chunk within each section (RecursiveCharacterTextSplitter, 1200 / 200).
    Code blocks are kept attached to surrounding prose — a lone code block
    with no context is nearly useless for retrieval.
6.  Assign deterministic IDs:  md::{filepath}::{section_slug}::{chunk_index}
7.  Embed, similarity check, upsert.

Called by
---------
The same batch script that calls ingest_pdf(). Both run over the knowledge
base folder in the repo.
"""

import hashlib
import logging
import os
import re
import uuid
from datetime import datetime

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from app.models import (
    BatchIngestionResult,
    IngestStatus,
    MarkdownPayload,
    NearDuplicate,
)
from app.services.embedding_service import embed_document
from app.stores.qdrant_store import check_near_duplicates, get_payload_by_id, upsert_point

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHUNK_SIZE    = 1200
CHUNK_OVERLAP = 200

# Heading levels to split on. H1, H2, H3 — kept as header metadata.
HEADERS_TO_SPLIT = [
    ("#",   "h1"),
    ("##",  "h2"),
    ("###", "h3"),
]

_header_splitter = MarkdownHeaderTextSplitter(
    headers_to_split_on=HEADERS_TO_SPLIT,
    strip_headers=False,   # keep the heading text inside the chunk content
)

_char_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    length_function=len,
    separators=["\n\n", "\n", " ", ""],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert a heading string to a URL-safe slug for use in point IDs."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:80]  # cap length so IDs don't become unwieldy


def _point_id(file_path: str, section_slug: str, chunk_index: int) -> str:
    """Generate deterministic UUID from file path, section, and chunk index."""
    normalised = file_path.replace("\\", "/")
    logical_id = f"md::{normalised}::{section_slug}::{chunk_index}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, logical_id))


def _probe_existing_hash(file_path: str) -> str | None:
    """Query Qdrant for any chunk from this file to get its content_hash."""
    from app.stores.qdrant_store import get_client
    from app.core.settings import settings
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    normalised = file_path.replace("\\", "/")
    client = get_client()

    # Search for any point with this source_uri
    results = client.scroll(
        collection_name=settings.qdrant_collection_name,
        scroll_filter=Filter(
            must=[FieldCondition(key="source_uri", match=MatchValue(value=normalised))]
        ),
        limit=1,
        with_payload=["content_hash"],
    )

    if results[0]:  # results is tuple (points, next_offset)
        return results[0][0].payload.get("content_hash")
    return None


def _extract_heading_meta(doc_metadata: dict) -> tuple[str, int]:
    """
    MarkdownHeaderTextSplitter stores heading levels as metadata keys
    matching the HEADERS_TO_SPLIT labels (h1, h2, h3).
    Returns (heading_text, heading_level) for the deepest heading present.
    """
    for level, key in [(3, "h3"), (2, "h2"), (1, "h1")]:
        if key in doc_metadata and doc_metadata[key]:
            return doc_metadata[key], level
    return "root", 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ingest_markdown(file_path: str) -> BatchIngestionResult:
    """
    Ingest a single Markdown file into Qdrant.

    Parameters
    ----------
    file_path : str
        Absolute or repo-relative path to the .md file.

    Returns
    -------
    BatchIngestionResult
    """
    filename = os.path.basename(file_path)
    log.info("Markdown ingest start: %s", file_path)

    # ------------------------------------------------------------------
    # File existence
    # ------------------------------------------------------------------
    if not os.path.exists(file_path):
        return BatchIngestionResult(
            file_path=file_path,
            status=IngestStatus.error,
            message=f"File not found: {file_path}",
        )

    # ------------------------------------------------------------------
    # Read file and compute hash
    # ------------------------------------------------------------------
    with open(file_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    if not raw_text.strip():
        return BatchIngestionResult(
            file_path=file_path,
            status=IngestStatus.skipped,
            message="File is empty.",
        )

    current_hash = hashlib.sha256(raw_text.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Stage 1 — content hash dedup
    # ------------------------------------------------------------------
    stored_hash = _probe_existing_hash(file_path)
    if stored_hash and stored_hash == current_hash:
        log.info("Markdown skipped (unchanged): %s", file_path)
        return BatchIngestionResult(
            file_path=file_path,
            status=IngestStatus.skipped,
            message="Content hash unchanged — no re-ingest needed.",
        )

    # ------------------------------------------------------------------
    # Split on heading boundaries
    # ------------------------------------------------------------------
    header_docs = _header_splitter.split_text(raw_text)

    # If the file has no headings at all, treat the whole file as one section
    if not header_docs:
        log.debug("No headings found in %s — treating as single section", file_path)

    chunks_ingested = 0
    chunks_skipped  = 0
    all_near_dupes: list[NearDuplicate] = []

    for header_doc in header_docs:
        section_text      = header_doc.page_content.strip()
        heading_text, level = _extract_heading_meta(header_doc.metadata)
        section_slug      = _slugify(heading_text)

        if not section_text:
            chunks_skipped += 1
            continue

        # ------------------------------------------------------------------
        # Chunk within the section
        # The section text already contains the heading at the top (strip_headers=False),
        # so every sub-chunk inherits the heading context naturally.
        # ------------------------------------------------------------------
        sub_chunks = _char_splitter.split_text(section_text)

        for chunk_idx, chunk_text in enumerate(sub_chunks):
            if not chunk_text.strip():
                chunks_skipped += 1
                continue

            point_id = _point_id(file_path, section_slug, chunk_idx)
            vector   = embed_document(chunk_text)

            # Stage 2 — similarity check
            near_dupes = check_near_duplicates(vector, own_point_id=point_id)
            all_near_dupes.extend(near_dupes)

            payload = MarkdownPayload(
                source_uri      = file_path.replace("\\", "/"),
                title           = f"{filename} — {heading_text}",
                date            = datetime.utcnow().isoformat(),
                content         = chunk_text,
                content_hash    = current_hash,
                filename        = filename,
                section_heading = heading_text,
                heading_level   = level,
                chunk_index     = chunk_idx,
            )

            upsert_point(point_id=point_id, vector=vector, payload=payload)
            chunks_ingested += 1
            log.debug("Upserted %s", point_id)

    log.info(
        "Markdown ingest complete: %s | ingested=%d skipped=%d near_dupes=%d",
        file_path, chunks_ingested, chunks_skipped, len(all_near_dupes),
    )

    return BatchIngestionResult(
        file_path       = file_path,
        status          = IngestStatus.ingested,
        chunks_ingested = chunks_ingested,
        chunks_skipped  = chunks_skipped,
        message         = f"Ingested {chunks_ingested} chunks.",
        near_duplicates = all_near_dupes,
    )
