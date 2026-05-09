"""
ingestion/ado.py

Azure DevOps work item ingestion pipeline.

Entry point
-----------
    ingest_ado_work_item(
        raw_work_item : dict,          # full ADO REST API response ($expand=all)
        org           : str,           # https://dev.azure.com/your-org
        project       : str,
        use_ai_summary: bool = False,
    ) -> IngestionResult

What it does
------------
1.  Parse the raw ADO response into typed models (no dict key guessing downstream).
2.  Build the deterministic point ID:  ado::{work_item_id}
3.  Stage 1 dedup — look up the existing point in Qdrant.
    Compare stored revision number. If unchanged, return skipped.
4.  Serialise the work item to a dense plain-text block (default path).
    Optional: send to LLM for a structured summary (use_ai_summary=True).
5.  Embed the text.
6.  Stage 2 similarity check — log near-duplicates, do not auto-delete.
7.  Upsert with full citation + embedding metadata.
8.  Return IngestionResult.

Called by
---------
POST /ingest/ado  (FastAPI route — not in this file)
The route validates the API key, calls this function, returns the result.
"""

import logging
import uuid

from app.models import (
    ADOPayload,
    ADOWorkItemRaw,
    IngestStatus,
    IngestionResult,
)
from app.services.embedding_service import embed_document
from app.stores.qdrant_store import check_near_duplicates, get_payload_by_id, upsert_point

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serialisation — default (no LLM)
# ---------------------------------------------------------------------------

def _serialise(item: ADOWorkItemRaw, org: str, project: str) -> str:
    """
    Flatten the work item into a structured plain-text block for embedding.
    No LLM involved.  Fields are presented in a consistent order so retrieval
    is predictable regardless of how ADO orders the JSON.
    """
    f = item.parsed_fields
    comments = item.last_five_comments

    comment_lines = "\n".join(
        f"  [{c.date_str}] {c.author}: {c.text}"
        for c in comments
        if c.text.strip()
    ) or "  None"

    tags_str = ", ".join(f.tags) if f.tags else "None"

    return (
        f"Work Item #{item.id} | {f.work_item_type} | {f.state}\n"
        f"Title: {f.title}\n"
        f"Area: {f.area_path}\n"
        f"Tags: {tags_str}\n"
        f"Assigned To: {f.assigned_to}\n"
        f"Created By: {f.created_by}\n"
        "\n"
        f"Description:\n{f.description or 'None'}\n"
        "\n"
        f"Acceptance Criteria:\n{f.acceptance_criteria or 'None'}\n"
        "\n"
        f"Recent Comments:\n{comment_lines}"
    ).strip()


# ---------------------------------------------------------------------------
# Optional LLM summarisation path
# ---------------------------------------------------------------------------

_ADO_SUMMARY_SYSTEM = (
    "You are a technical knowledge base assistant for a software engineering team. "
    "Extract structured information from ADO work items concisely and accurately."
)

_ADO_SUMMARY_USER = """\
Summarise the following Azure DevOps work item for a searchable knowledge base.

Return exactly these four sections:

1. Summary — two sentences: what the work item is and its current status.
2. Technical Scope — key systems, services, APIs, or features involved.
3. Acceptance Criteria — concise bullet list of done conditions.
4. Notes — any important decisions, blockers, or context from the comments.

Work Item:
{serialised}
"""


def _summarise_with_llm(serialised: str) -> str:
    """
    Call Azure OpenAI to summarise the serialised work item.
    Raises on failure — caller falls back to serialised text.
    Import here (not at module level) so local dev without Azure creds still works.
    """
    from app.services.llm_service import get_client
    from app.core.settings import settings

    client = get_client()
    response = client.chat.completions.create(
        model=settings.azure_openai_deployment_name,
        max_tokens=600,
        temperature=0.2,
        messages=[
            {"role": "system", "content": _ADO_SUMMARY_SYSTEM},
            {"role": "user",   "content": _ADO_SUMMARY_USER.format(serialised=serialised)},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM returned empty ADO summary")
    return content.strip()


# ---------------------------------------------------------------------------
# Point ID
# ---------------------------------------------------------------------------

def _point_id(work_item_id: int) -> str:
    """Generate deterministic UUID from work item ID."""
    logical_id = f"ado::{work_item_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, logical_id))


# ---------------------------------------------------------------------------
# Participants helper
# ---------------------------------------------------------------------------

def _collect_participants(item: ADOWorkItemRaw) -> list[str]:
    names: set[str] = set()
    f = item.parsed_fields
    if f.assigned_to and f.assigned_to != "Unassigned":
        names.add(f.assigned_to)
    for c in item.parsed_comments:
        if c.author and c.author != "Unknown":
            names.add(c.author)
    return sorted(names)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ingest_ado_work_item(
    raw_work_item: dict,
    org: str,
    project: str,
    use_ai_summary: bool = False,
) -> IngestionResult:
    """
    Ingest a single ADO work item into Qdrant.

    Parameters
    ----------
    raw_work_item : dict
        The full response body from:
        GET /{org}/{project}/_apis/wit/workitems/{id}?$expand=all&api-version=7.1
        Pass it as-is — parsing happens here.

    org : str
        ADO organisation URL, e.g. "https://dev.azure.com/your-org"

    project : str
        ADO project name.

    use_ai_summary : bool
        If True, send the serialised text to Azure OpenAI for a structured summary.
        The summary is embedded instead of the raw serialised text.
        Falls back to serialised text if the LLM call fails.

    Returns
    -------
    IngestionResult
    """
    # ------------------------------------------------------------------
    # Parse raw response
    # ------------------------------------------------------------------
    try:
        item = ADOWorkItemRaw.model_validate(raw_work_item)
    except Exception as exc:
        return IngestionResult(
            status=IngestStatus.error,
            message=f"Failed to parse ADO work item: {exc}",
        )

    f          = item.parsed_fields
    point_id   = _point_id(item.id)
    log.info("ADO ingest start: work_item_id=%d rev=%d", item.id, item.rev)

    # ------------------------------------------------------------------
    # Stage 1 — revision dedup
    # Check if we already have this revision stored.
    # ------------------------------------------------------------------
    existing = get_payload_by_id(point_id)
    if existing:
        stored_hash = existing.get("content_hash", "")
        current_hash = str(item.rev)
        if stored_hash == current_hash:
            log.info("ADO skipped (revision unchanged): work_item_id=%d rev=%d", item.id, item.rev)
            return IngestionResult(
                status=IngestStatus.skipped,
                point_id=point_id,
                message=f"Revision {item.rev} already ingested.",
            )

    # ------------------------------------------------------------------
    # Serialise
    # ------------------------------------------------------------------
    serialised = _serialise(item, org, project)

    # ------------------------------------------------------------------
    # Optional LLM summary
    # ------------------------------------------------------------------
    is_summary  = False
    embed_text  = serialised

    if use_ai_summary:
        try:
            embed_text = _summarise_with_llm(serialised)
            is_summary = True
            log.info("ADO LLM summary generated for work_item_id=%d", item.id)
        except Exception as exc:
            log.warning(
                "ADO LLM summary failed for work_item_id=%d — falling back to serialised text. Error: %s",
                item.id, exc,
            )

    # ------------------------------------------------------------------
    # Embed
    # ------------------------------------------------------------------
    vector = embed_document(embed_text)

    # ------------------------------------------------------------------
    # Stage 2 — similarity check
    # ------------------------------------------------------------------
    near_dupes = check_near_duplicates(vector, own_point_id=point_id)

    # ------------------------------------------------------------------
    # Build payload
    # ------------------------------------------------------------------
    created_date_str = f.created_date.strftime("%Y-%m-%d") if f.created_date else None

    payload = ADOPayload(
        source_uri      = item.browser_url(org, project),
        title           = f"#{item.id} {f.title}",
        author          = f.created_by,
        date            = created_date_str,
        content         = embed_text,
        content_hash    = str(item.rev),    # revision number is the change marker
        is_summary      = is_summary,
        work_item_id    = item.id,
        work_item_type  = f.work_item_type,
        work_item_state = f.state,
        tags            = f.tags,
        participants    = _collect_participants(item),
    )

    # ------------------------------------------------------------------
    # Upsert — deterministic ID means this replaces any existing entry
    # ------------------------------------------------------------------
    upsert_point(point_id=point_id, vector=vector, payload=payload)

    log.info(
        "ADO ingest complete: work_item_id=%d near_dupes=%d is_summary=%s",
        item.id, len(near_dupes), is_summary,
    )

    return IngestionResult(
        status          = IngestStatus.ingested,
        point_id        = point_id,
        message         = f"Ingested work item #{item.id} (rev {item.rev}).",
        near_duplicates = near_dupes,
    )
