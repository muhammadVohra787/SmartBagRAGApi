"""
ingestion/pdf.py

PDF ingestion pipeline.

Entry point
-----------
    ingest_pdf(file_path: str) -> BatchIngestionResult

What it does
------------
1.  Compute SHA-256 of file contents.
2.  Check Qdrant for an existing chunk from this file — if the stored content_hash
    matches, skip the whole file (nothing changed).
3.  Open with PyMuPDF. Extract text page by page.
4.  Skip pages with no usable text (scanned images, blank pages).
5.  Chunk each page with RecursiveCharacterTextSplitter (1200 chars / 200 overlap).
6.  Assign deterministic point IDs:  pdf::{filepath}::{page}::{chunk_index}
7.  Embed each chunk.
8.  Stage 2 similarity check — log near-duplicates, do not auto-delete.
9.  Upsert into Qdrant with full citation + embedding metadata.

Called by
---------
The batch ingestion script that walks the knowledge base repo folder.
Not a FastAPI route — runs as a standalone script or CI step.
"""

from datetime import datetime
import hashlib
import logging
import os
import uuid

import fitz  # PyMuPDF
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.models import (
    BatchIngestionResult,
    IngestStatus,
    NearDuplicate,
    PDFPayload,
)
from app.services.embedding_service import embed_document
from app.stores.qdrant_store import (
    check_near_duplicates,
    get_payload_by_id,
    upsert_point,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHUNK_SIZE    = 1200   # characters
CHUNK_OVERLAP = 200    # characters
MIN_PAGE_CHARS = 50    # pages with fewer chars than this are skipped (blank / image-only)

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    length_function=len,
    separators=["\n\n", "\n", " ", ""],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _point_id(file_path: str, page: int, chunk_index: int) -> str:
    """Generate deterministic UUID from file path, page, and chunk index."""
    normalised = file_path.replace("\\", "/")
    logical_id = f"pdf::{normalised}::{page}::{chunk_index}"
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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ingest_pdf(file_path: str) -> BatchIngestionResult:
    """
    Ingest a single PDF file into Qdrant.

    Parameters
    ----------
    file_path : str
        Absolute or repo-relative path to the PDF.
        Used verbatim in point IDs and source_uri — keep it consistent.

    Returns
    -------
    BatchIngestionResult
    """
    filename = os.path.basename(file_path)
    log.info("PDF ingest start: %s", file_path)

    # ------------------------------------------------------------------
    # Stage 0 — file existence
    # ------------------------------------------------------------------
    if not os.path.exists(file_path):
        return BatchIngestionResult(
            file_path=file_path,
            status=IngestStatus.error,
            message=f"File not found: {file_path}",
        )

    # ------------------------------------------------------------------
    # Stage 1 — content hash dedup
    # ------------------------------------------------------------------
    current_hash = _sha256(file_path)
    stored_hash  = _probe_existing_hash(file_path)

    if stored_hash and stored_hash == current_hash:
        log.info("PDF skipped (unchanged): %s", file_path)
        return BatchIngestionResult(
            file_path=file_path,
            status=IngestStatus.skipped,
            message="Content hash unchanged — no re-ingest needed.",
        )

    # ------------------------------------------------------------------
    # Extract text with PyMuPDF
    # ------------------------------------------------------------------
    try:
        doc = fitz.open(file_path)
    except Exception as exc:
        return BatchIngestionResult(
            file_path=file_path,
            status=IngestStatus.error,
            message=f"Failed to open PDF: {exc}",
        )

    total_pages      = doc.page_count
    chunks_ingested  = 0
    chunks_skipped   = 0
    all_near_dupes:  list[NearDuplicate] = []

    for page_num in range(total_pages):
        page      = doc[page_num]
        page_text = page.get_text("text").strip()         # plain text extraction

        if len(page_text) < MIN_PAGE_CHARS:
            log.debug("PDF page %d skipped (too short or image-only): %s", page_num, file_path)
            chunks_skipped += 1
            continue

        chunks = _splitter.split_text(page_text)

        for chunk_idx, chunk_text in enumerate(chunks):
            if not chunk_text.strip():
                chunks_skipped += 1
                continue

            point_id = _point_id(file_path, page_num, chunk_idx)
            vector   = embed_document(chunk_text)

            # Stage 2 — similarity check against different documents
            near_dupes = check_near_duplicates(vector, own_point_id=point_id)
            all_near_dupes.extend(near_dupes)

            payload = PDFPayload(
                source_uri    = file_path.replace("\\", "/"),
                title         = f"{filename} — p.{page_num + 1}",
                content       = chunk_text,
                date          = datetime.utcnow().isoformat(),
                content_hash  = current_hash,
                filename      = filename,
                page_number   = page_num,
                total_pages   = total_pages,
                chunk_index   = chunk_idx,
            )

            upsert_point(point_id=point_id, vector=vector, payload=payload)
            chunks_ingested += 1
            log.debug("Upserted %s", point_id)

    doc.close()

    log.info(
        "PDF ingest complete: %s | ingested=%d skipped=%d near_dupes=%d",
        file_path, chunks_ingested, chunks_skipped, len(all_near_dupes),
    )
    
  # If no chunks were ingested, treat as skipped (likely image-only PDF)
    if chunks_ingested == 0:
        return BatchIngestionResult(
            file_path       = file_path,
            status          = IngestStatus.skipped,
            chunks_ingested = 0,
            chunks_skipped  = chunks_skipped,
            message         = "No extractable text found (image-only or blank PDF).",
            near_duplicates = all_near_dupes,
        )

    return BatchIngestionResult(
        file_path       = file_path,
        status          = IngestStatus.ingested,
        chunks_ingested = chunks_ingested,
        chunks_skipped  = chunks_skipped,
        message         = f"Ingested {chunks_ingested} chunks from {total_pages} pages.",
        near_duplicates = all_near_dupes,
    )
