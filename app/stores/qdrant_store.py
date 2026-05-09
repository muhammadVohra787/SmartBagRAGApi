"""
services/qdrant_store.py

All Qdrant interactions in one place.
The pipelines call these functions — they never touch the Qdrant client directly.

Key design decisions:
- Deterministic point IDs mean upsert = create-or-replace, no pre-delete needed.
- Payload is stored as a flat dict (model.model_dump()) — Qdrant stores it as JSON.
- The 'content' field in the payload is what the LLM reads at query time.
  The vector is what Qdrant searches against. Both come from the same text.
"""

import logging
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    ScoredPoint,
    VectorParams,
)

from config import settings
from models import (
    EMBEDDING_DIMS,
    SIMILARITY_DUPE_THRESHOLD,
    BasePayload,
    NearDuplicate,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------

def _make_client() -> QdrantClient:
    kwargs: dict[str, Any] = {
        "host": settings.qdrant_host,
        "port": settings.qdrant_port,
    }
    if settings.qdrant_api_key:
        kwargs["api_key"] = settings.qdrant_api_key
    return QdrantClient(**kwargs)


_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = _make_client()
    return _client


# ---------------------------------------------------------------------------
# Collection bootstrap
# Call once on startup (or in a setup script before first ingest).
# ---------------------------------------------------------------------------

def ensure_collection() -> None:
    """
    Create the Qdrant collection and all payload indexes if they don't exist.
    Safe to call on every startup — does nothing if already created.
    """
    client = get_client()
    collection = settings.qdrant_collection

    existing = {c.name for c in client.get_collections().collections}
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE),
        )
        log.info("Created Qdrant collection '%s' (%d dims)", collection, EMBEDDING_DIMS)

    # Payload indexes — dramatically improve filter performance.
    # create_payload_index is idempotent; safe to call every time.
    indexes = {
        "source_type":      PayloadSchemaType.KEYWORD,
        "work_item_id":     PayloadSchemaType.INTEGER,
        "thread_id":        PayloadSchemaType.KEYWORD,
        "filename":         PayloadSchemaType.KEYWORD,
        "embedding_version":PayloadSchemaType.INTEGER,
        "work_item_state":  PayloadSchemaType.KEYWORD,
        "tags":             PayloadSchemaType.KEYWORD,
    }
    for field, schema in indexes.items():
        client.create_payload_index(
            collection_name=collection,
            field_name=field,
            field_schema=schema,
        )


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def upsert_point(
    point_id: str,
    vector: list[float],
    payload: BasePayload,
) -> None:
    """
    Upsert a single document. Because IDs are deterministic, this
    naturally overwrites any existing entry with the same ID.
    """
    client = get_client()
    client.upsert(
        collection_name=settings.qdrant_collection,
        points=[
            PointStruct(
                id=point_id,
                vector=vector,
                payload=payload.model_dump(mode="json"),
            )
        ],
    )
    log.debug("Upserted point '%s' (source_type=%s)", point_id, payload.source_type)


# ---------------------------------------------------------------------------
# Dedup helpers
# ---------------------------------------------------------------------------

def get_payload_by_id(point_id: str) -> dict[str, Any] | None:
    """
    Return the stored payload for a point, or None if it doesn't exist.
    Used for Stage 1 dedup: compare content_hash / revision before re-embedding.
    """
    client = get_client()
    results = client.retrieve(
        collection_name=settings.qdrant_collection,
        ids=[point_id],
        with_payload=True,
    )
    if not results:
        return None
    return results[0].payload  # type: ignore[return-value]


def check_near_duplicates(
    vector: list[float],
    own_point_id: str,
    top_k: int = 5,
) -> list[NearDuplicate]:
    """
    Stage 2 dedup: search for similar vectors from *different* point IDs.
    Returns NearDuplicate entries for any match above SIMILARITY_DUPE_THRESHOLD.
    Caller decides what to do with them (currently: log only).
    """
    client = get_client()
    results: list[ScoredPoint] = client.search(
        collection_name=settings.qdrant_collection,
        query_vector=vector,
        limit=top_k,
        with_payload=["title", "source_type"],
    )
    dupes: list[NearDuplicate] = []
    for r in results:
        if str(r.id) == own_point_id:
            continue  # same document — expected
        if r.score >= SIMILARITY_DUPE_THRESHOLD:
            dupes.append(
                NearDuplicate(
                    existing_point_id=str(r.id),
                    score=r.score,
                    existing_title=r.payload.get("title") if r.payload else None,
                )
            )
            log.warning(
                "Near-duplicate detected: new='%s' existing='%s' score=%.4f title='%s'",
                own_point_id,
                r.id,
                r.score,
                r.payload.get("title", "?") if r.payload else "?",
            )
    return dupes


# ---------------------------------------------------------------------------
# Search (used by query endpoints)
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD = 0.65


def search(
    query_vector: list[float],
    top_k: int = 20,
    source_type: str | None = None,
    extra_filters: list[FieldCondition] | None = None,
) -> list[ScoredPoint]:
    """
    Search the collection. Returns up to top_k results.
    Applies source_type filter if provided, plus any extra payload filters.
    The caller is responsible for the confidence threshold check.
    """
    client = get_client()

    must_conditions: list[FieldCondition] = []
    if source_type:
        must_conditions.append(
            FieldCondition(key="source_type", match=MatchValue(value=source_type))
        )
    if extra_filters:
        must_conditions.extend(extra_filters)

    filt = Filter(must=must_conditions) if must_conditions else None

    return client.search(
        collection_name=settings.qdrant_collection,
        query_vector=query_vector,
        limit=top_k,
        query_filter=filt,
        with_payload=True,
    )


def is_above_confidence(results: list[ScoredPoint]) -> bool:
    """True if the top result meets the minimum confidence threshold."""
    return bool(results) and results[0].score >= CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Deletion helpers (for maintenance / re-indexing scripts)
# ---------------------------------------------------------------------------

def delete_by_filename(filename: str) -> int:
    """
    Delete all chunks belonging to a specific file.
    Used when a PDF or Markdown file is removed from the repo.
    Returns the number of deleted points.
    """
    client = get_client()
    filt = Filter(must=[FieldCondition(key="filename", match=MatchValue(value=filename))])
    result = client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=filt,  # type: ignore[arg-type]
    )
    deleted = result.result if hasattr(result, "result") else 0
    log.info("Deleted %s points for filename='%s'", deleted, filename)
    return deleted or 0


def delete_point(point_id: str) -> None:
    client = get_client()
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=PointIdsList(points=[point_id]),
    )
    log.info("Deleted point '%s'", point_id)
