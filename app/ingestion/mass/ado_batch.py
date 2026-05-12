"""
app/ingestion/mass/ado_batch.py

Mass ingestion pipeline for ADO resolved/closed bug tickets.

Entry point
-----------
    ingest_ado_batch(
        items   : list[dict],   # raw ADO REST API responses ($expand=all)
        org     : str,
        project : str,
    ) -> ADOBatchSummary

Pipeline per item
-----------------
1.  Parse into ADOWorkItemRaw.
2.  Pre-filter: state must be Resolved or Closed, type must be Bug or Issue.
3.  Heuristic quality gate:
        noise   → skip, log it.
        low     → heuristic serialisation only, no LLM. Upsert with low priority.
        medium  → LLM summary. Upsert.
        high    → LLM summary. Upsert with high priority flag.
4.  Stage 1 dedup — revision check against Qdrant.
5.  Build content (serialised or LLM summary).
6.  Embed.
7.  Stage 2 similarity check.
8.  Upsert.

Progress is logged per item. The returned ADOBatchSummary gives counts by tier
so you can see what the gate filtered out across your 1,300 tickets.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from app.ingestion.mass.keywords import ADO_DISQUALIFY, ADO_NOISE
from app.ingestion.mass.prompts import (
    ADO_BUG_SYSTEM,
    ADO_BUG_USER,
    format_ado_summary,
    parse_ado_response,
)
from app.ingestion.mass.quality import ADOQualityResult, score_ado_item
from app.models import ADOPayload, ADOWorkItemRaw, IngestStatus, IngestionResult, NearDuplicate
from app.services.embedding_service import embed_document
from app.services.llm_service import get_client
from app.stores.qdrant_store import check_near_duplicates, get_payload_by_id, upsert_point
from app.core.settings import settings

log = logging.getLogger(__name__)

# States and types this pipeline is designed for
VALID_STATES = {"resolved", "closed"}
VALID_TYPES  = {"bug", "issue"}

# Delay between LLM calls (seconds) — respects Azure OpenAI rate limits
LLM_CALL_DELAY = 0.5


# =============================================================================
# Batch summary
# =============================================================================

@dataclass
class ADOBatchSummary:
    total:          int = 0
    ingested_high:  int = 0
    ingested_medium:int = 0
    ingested_low:   int = 0
    skipped_noise:  int = 0
    skipped_dedup:  int = 0
    skipped_state:  int = 0
    errors:         int = 0
    near_dupes_logged: int = 0
    results:        list[IngestionResult] = field(default_factory=list)

    @property
    def total_ingested(self) -> int:
        return self.ingested_high + self.ingested_medium + self.ingested_low

    def log_summary(self) -> None:
        log.info(
            "ADO batch complete | total=%d ingested=%d (high=%d medium=%d low=%d) "
            "noise=%d dedup=%d state_skip=%d errors=%d near_dupes=%d",
            self.total,
            self.total_ingested,
            self.ingested_high, self.ingested_medium, self.ingested_low,
            self.skipped_noise, self.skipped_dedup, self.skipped_state,
            self.errors, self.near_dupes_logged,
        )


# =============================================================================
# Serialisation (matches ingestion/ado.py but includes all comment text)
# =============================================================================

def _serialise(item: ADOWorkItemRaw, org: str, project: str) -> str:
    f        = item.parsed_fields
    comments = item.last_five_comments

    comment_lines = "\n".join(
        f"  [{c.date_str}] {c.author}: {c.text}"
        for c in comments if c.text.strip()
    ) or "  None"

    return (
        f"Work Item #{item.id} | {f.work_item_type} | {f.state}\n"
        f"Title: {f.title}\n"
        f"Area: {f.area_path}\n"
        f"Tags: {', '.join(f.tags) or 'None'}\n"
        f"Assigned To: {f.assigned_to}\n"
        f"Created By: {f.created_by}\n"
        "\n"
        f"Description:\n{f.description or 'None'}\n"
        "\n"
        f"Acceptance Criteria:\n{f.acceptance_criteria or 'None'}\n"
        "\n"
        f"Recent Comments:\n{comment_lines}"
    ).strip()


# =============================================================================
# LLM summary
# =============================================================================

def _llm_summary(serialised: str) -> tuple[str, int]:
    """
    Call Azure OpenAI. Returns (embed_text, llm_quality_score).
    Raises on failure — caller falls back to serialised text.
    """
    client   = get_client()
    response = client.chat.completions.create(
        model=settings.azure_openai_deployment_name,
        max_tokens=700,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": ADO_BUG_SYSTEM},
            {"role": "user",   "content": ADO_BUG_USER.format(serialised=serialised)},
        ],
    )
    raw     = response.choices[0].message.content or "{}"
    parsed  = parse_ado_response(raw)
    text    = format_ado_summary(parsed)
    score   = int(parsed.get("quality_score", 3))
    return text, score


# =============================================================================
# Single item processor
# =============================================================================

def _process_one(
    raw_item: dict,
    org:      str,
    project:  str,
    summary:  ADOBatchSummary,
    index:    int,
    total:    int,
) -> IngestionResult:

    # -- Parse ----------------------------------------------------------------
    try:
        item = ADOWorkItemRaw.model_validate(raw_item)
    except Exception as exc:
        summary.errors += 1
        log.error("[%d/%d] Parse error: %s", index, total, exc)
        return IngestionResult(status=IngestStatus.error, message=str(exc))

    f        = item.parsed_fields
    point_id = f"ado::{item.id}"
    log.info("[%d/%d] Processing #%d %s | %s", index, total, item.id, f.work_item_type, f.state)

    # -- Pre-filter: state + type ---------------------------------------------
    if f.state.lower() not in VALID_STATES:
        summary.skipped_state += 1
        log.debug("Skipped #%d — state '%s' not in %s", item.id, f.state, VALID_STATES)
        return IngestionResult(
            status=IngestStatus.skipped,
            point_id=point_id,
            message=f"State '{f.state}' not in scope.",
        )

    if f.work_item_type.lower() not in VALID_TYPES:
        summary.skipped_state += 1
        log.debug("Skipped #%d — type '%s' not in %s", item.id, f.work_item_type, VALID_TYPES)
        return IngestionResult(
            status=IngestStatus.skipped,
            point_id=point_id,
            message=f"Type '{f.work_item_type}' not in scope.",
        )

    # -- Heuristic quality gate -----------------------------------------------
    comments_raw = [
        {"text": c.text, "author": c.author, "date": c.date_str}
        for c in item.parsed_comments
    ]
    quality: ADOQualityResult = score_ado_item(f.model_dump() if hasattr(f, 'model_dump') else item.fields, comments_raw)

    log.info(
        "  Quality: score=%.2f tier=%s | %s",
        quality.score, quality.tier, " | ".join(quality.reasons),
    )

    if quality.should_skip:
        summary.skipped_noise += 1
        return IngestionResult(
            status=IngestStatus.skipped,
            point_id=point_id,
            message=f"Noise tier (score={quality.score:.2f}). Reasons: {quality.reasons}",
        )

    # -- Stage 1 dedup: revision check ----------------------------------------
    existing = get_payload_by_id(point_id)
    if existing and existing.get("content_hash") == str(item.rev):
        summary.skipped_dedup += 1
        return IngestionResult(
            status=IngestStatus.skipped,
            point_id=point_id,
            message=f"Revision {item.rev} already ingested.",
        )

    # -- Build content --------------------------------------------------------
    serialised    = _serialise(item, org, project)
    is_summary    = False
    llm_score     = None
    embed_text    = serialised

    if quality.use_llm:
        try:
            embed_text, llm_score = _llm_summary(serialised)
            is_summary = True
            time.sleep(LLM_CALL_DELAY)   # rate limit courtesy
            log.info("  LLM summary done. LLM quality score: %s/5", llm_score)
        except Exception as exc:
            log.warning("  LLM failed for #%d — using serialised text. Error: %s", item.id, exc)

    # -- Embed + similarity check ---------------------------------------------
    vector     = embed_document(embed_text)
    near_dupes: list[NearDuplicate] = check_near_duplicates(vector, own_point_id=point_id)
    if near_dupes:
        summary.near_dupes_logged += len(near_dupes)

    # -- Build payload --------------------------------------------------------
    created_str = f.created_date.strftime("%Y-%m-%d") if f.created_date else None

    payload = ADOPayload(
        source_uri      = item.browser_url(org, project),
        title           = f"#{item.id} {f.title}",
        author          = f.created_by,
        date            = created_str,
        content         = embed_text,
        content_hash    = str(item.rev),
        is_summary      = is_summary,
        work_item_id    = item.id,
        work_item_type  = f.work_item_type,
        work_item_state = f.state,
        tags            = f.tags,
        participants    = sorted({
            f.assigned_to,
            *[c.author for c in item.parsed_comments if c.author != "Unknown"]
        } - {"Unassigned"}),
    )

    # Store quality metadata in payload as extra fields
    payload_dict = payload.model_dump(mode="json")
    payload_dict["quality_score"]     = quality.score
    payload_dict["quality_tier"]      = quality.tier
    payload_dict["quality_reasons"]   = quality.reasons
    if llm_score is not None:
        payload_dict["llm_quality_score"] = llm_score

    # Upsert directly with the enriched dict (Qdrant accepts any payload)
    from qdrant_client.models import PointStruct
    from app.stores.qdrant_store import get_client as get_qdrant
    get_qdrant().upsert(
        collection_name=settings.qdrant_collection_name,
        points=[PointStruct(id=point_id, vector=vector, payload=payload_dict)],
    )

    # -- Update summary counters ----------------------------------------------
    if quality.tier == "high":
        summary.ingested_high += 1
    elif quality.tier == "medium":
        summary.ingested_medium += 1
    else:
        summary.ingested_low += 1

    return IngestionResult(
        status          = IngestStatus.ingested,
        point_id        = point_id,
        message         = f"#{item.id} ingested (tier={quality.tier} score={quality.score:.2f})",
        near_duplicates = near_dupes,
    )


# =============================================================================
# Batch entry point
# =============================================================================

def ingest_ado_batch(
    items:   list[dict],
    org:     str,
    project: str,
) -> ADOBatchSummary:
    """
    Mass-ingest a list of ADO work items.

    Parameters
    ----------
    items : list[dict]
        Raw ADO REST API work item responses. Each must have been fetched with
        $expand=all so comments, relations, and all fields are present.
    org : str
        ADO organisation URL, e.g. "https://dev.azure.com/your-org"
    project : str
        ADO project name.

    Returns
    -------
    ADOBatchSummary
        Counts by outcome. Call .log_summary() to print to the logger.
    """
    total   = len(items)
    summary = ADOBatchSummary(total=total)

    log.info("ADO batch ingest start: %d items | org=%s project=%s", total, org, project)

    for i, raw_item in enumerate(items, start=1):
        result = _process_one(raw_item, org, project, summary, i, total)
        summary.results.append(result)

    summary.log_summary()
    return summary
