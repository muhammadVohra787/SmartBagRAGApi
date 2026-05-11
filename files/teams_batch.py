"""
app/ingestion/mass/teams_batch.py

Mass ingestion pipeline for Teams channel threads.

Entry point
-----------
    ingest_teams_batch(
        threads : list[TeamsBatchThread],
    ) -> TeamsBatchSummary

Input shape
-----------
Each thread is a TeamsBatchThread TypedDict:
    {
        "raw_messages":  list[dict],   # Graph message objects, root first
        "thread_id":     str,
        "channel_id":    str,
        "channel_name":  str,
        "team_id":       str,
    }

Pipeline per thread
-------------------
1.  Pre-filter: must have > 1 reply after parsing.
2.  Parse GraphMessage models and clean HTML with html2text.
3.  Filter out system messages and messages under 10 words.
4.  Heuristic quality gate:
        noise   → skip, log.
        low     → store flattened raw text only. No LLM.
        medium  → LLM summary.
        high    → LLM summary.
5.  Stage 1 dedup — revision marker check against Qdrant.
6.  Trim to 12,000 tokens if needed.
7.  Build content (raw or LLM summary).
8.  Embed.
9.  Stage 2 similarity check.
10. Upsert.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import TypedDict

import html2text
import tiktoken

from app.ingestion.mass.prompts import (
    TEAMS_THREAD_SYSTEM,
    TEAMS_THREAD_USER,
    format_teams_summary,
    parse_teams_response,
)
from app.ingestion.mass.quality import TeamsQualityResult, score_teams_thread
from app.models import GraphMessage, IngestStatus, IngestionResult, NearDuplicate, TeamsPayload
from app.services.embedder import embed_document
from app.services.llm import get_client
from app.services.qdrant_store import check_near_duplicates, get_payload_by_id, upsert_point
from app.config import settings

log = logging.getLogger(__name__)

MIN_MESSAGE_WORDS = 10
MAX_TOKENS        = 12_000
TIKTOKEN_ENCODING = "cl100k_base"
LLM_CALL_DELAY    = 0.5

# ---------------------------------------------------------------------------
# html2text setup
# ---------------------------------------------------------------------------
_h2t = html2text.HTML2Text()
_h2t.ignore_links    = True
_h2t.ignore_images   = True
_h2t.ignore_emphasis = True
_h2t.body_width      = 0
_h2t.unicode_snob    = True


# =============================================================================
# Input type
# =============================================================================

class TeamsBatchThread(TypedDict):
    raw_messages: list[dict]
    thread_id:    str
    channel_id:   str
    channel_name: str
    team_id:      str


# =============================================================================
# Batch summary
# =============================================================================

@dataclass
class TeamsBatchSummary:
    total:           int = 0
    ingested_high:   int = 0
    ingested_medium: int = 0
    ingested_low:    int = 0
    skipped_noise:   int = 0
    skipped_dedup:   int = 0
    skipped_no_reply:int = 0
    errors:          int = 0
    near_dupes_logged: int = 0
    results:         list[IngestionResult] = field(default_factory=list)

    @property
    def total_ingested(self) -> int:
        return self.ingested_high + self.ingested_medium + self.ingested_low

    def log_summary(self) -> None:
        log.info(
            "Teams batch complete | total=%d ingested=%d (high=%d medium=%d low=%d) "
            "noise=%d no_reply=%d dedup=%d errors=%d near_dupes=%d",
            self.total,
            self.total_ingested,
            self.ingested_high, self.ingested_medium, self.ingested_low,
            self.skipped_noise, self.skipped_no_reply, self.skipped_dedup,
            self.errors, self.near_dupes_logged,
        )


# =============================================================================
# Thread utilities
# =============================================================================

def _sanitise(content: str, content_type: str) -> str:
    if content_type == "html":
        return _h2t.handle(content).strip()
    return content.strip()


def _clean_messages(
    messages: list[GraphMessage],
) -> dict[str, str]:
    """Return message.id → cleaned text for all messages that pass quality filter."""
    cleaned: dict[str, str] = {}
    for msg in messages:
        if not msg.is_user_message:
            continue
        raw = msg.body.content or ""
        if not raw.strip():
            continue
        text = _sanitise(raw, msg.body.contentType)
        if text and len(text.split()) >= MIN_MESSAGE_WORDS:
            cleaned[msg.id] = text
    return cleaned


def _flatten(messages: list[GraphMessage], cleaned: dict[str, str]) -> str:
    lines = []
    for msg in messages:
        text = cleaned.get(msg.id)
        if text:
            date = msg.createdDateTime.strftime("%Y-%m-%d")
            lines.append(f"[{date}] {msg.sender}: {text}")
    return "\n\n".join(lines)


def _token_count(text: str) -> int:
    return len(tiktoken.get_encoding(TIKTOKEN_ENCODING).encode(text))


def _trim_to_limit(
    messages: list[GraphMessage],
    cleaned:  dict[str, str],
) -> list[GraphMessage]:
    trimmed = list(messages)
    while len(trimmed) > 1 and _token_count(_flatten(trimmed, cleaned)) > MAX_TOKENS:
        trimmed.pop(1)
    return trimmed


def _revision_marker(messages: list[GraphMessage], user_count: int) -> str:
    last_ts = max(
        (m.lastModifiedDateTime or m.createdDateTime for m in messages),
        default=messages[-1].createdDateTime,
    )
    return f"{user_count}::{last_ts.isoformat()}"


def _point_id(thread_id: str) -> str:
    return f"teams::{thread_id}"


# =============================================================================
# LLM summary
# =============================================================================

def _llm_summary(thread_text: str) -> tuple[str, int]:
    """
    Returns (embed_text, llm_quality_score).
    Raises on failure — caller falls back to flat text.
    """
    client   = get_client()
    response = client.chat.completions.create(
        model=settings.azure_openai_deployment,
        max_tokens=900,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": TEAMS_THREAD_SYSTEM},
            {"role": "user",   "content": TEAMS_THREAD_USER.format(thread_text=thread_text)},
        ],
    )
    raw    = response.choices[0].message.content or "{}"
    parsed = parse_teams_response(raw)
    text   = format_teams_summary(parsed)
    score  = int(parsed.get("quality_score", 3))
    return text, score


# =============================================================================
# Single thread processor
# =============================================================================

def _process_one(
    thread:  TeamsBatchThread,
    summary: TeamsBatchSummary,
    index:   int,
    total:   int,
) -> IngestionResult:

    thread_id    = thread["thread_id"]
    channel_id   = thread["channel_id"]
    channel_name = thread["channel_name"]
    point_id     = _point_id(thread_id)

    log.info("[%d/%d] Processing thread %s | channel=%s", index, total, thread_id, channel_name)

    # -- Parse ----------------------------------------------------------------
    try:
        messages: list[GraphMessage] = [
            GraphMessage.model_validate(m) for m in thread["raw_messages"]
        ]
    except Exception as exc:
        summary.errors += 1
        log.error("  Parse error: %s", exc)
        return IngestionResult(status=IngestStatus.error, message=str(exc))

    # -- Pre-filter: requires >1 reply ----------------------------------------
    cleaned_map   = _clean_messages(messages)
    user_messages = [m for m in messages if m.id in cleaned_map]
    reply_count   = len(user_messages) - 1

    if reply_count < 1:
        summary.skipped_no_reply += 1
        log.debug("  Skipped — no replies after cleaning")
        return IngestionResult(
            status=IngestStatus.skipped,
            point_id=point_id,
            message="No replies after cleaning.",
        )

    # -- Heuristic quality gate -----------------------------------------------
    quality: TeamsQualityResult = score_teams_thread(messages, cleaned_map)

    log.info(
        "  Quality: score=%.2f tier=%s replies=%d participants=%d | %s",
        quality.score, quality.tier,
        quality.reply_count, quality.unique_participants,
        " | ".join(quality.reasons),
    )

    if quality.should_skip:
        summary.skipped_noise += 1
        return IngestionResult(
            status=IngestStatus.skipped,
            point_id=point_id,
            message=f"Noise tier (score={quality.score:.2f}). Reasons: {quality.reasons}",
        )

    # -- Stage 1 dedup --------------------------------------------------------
    revision = _revision_marker(messages, len(user_messages))
    existing = get_payload_by_id(point_id)
    if existing and existing.get("content_hash") == revision:
        summary.skipped_dedup += 1
        return IngestionResult(
            status=IngestStatus.skipped,
            point_id=point_id,
            message="Thread unchanged since last ingest.",
        )

    # -- Trim + flatten -------------------------------------------------------
    trimmed  = _trim_to_limit(user_messages, cleaned_map)
    flat     = _flatten(trimmed, cleaned_map)

    if len(trimmed) < len(user_messages):
        log.info("  Trimmed from %d to %d messages to fit token limit", len(user_messages), len(trimmed))

    # -- Build content --------------------------------------------------------
    is_summary  = False
    llm_score   = None
    embed_text  = flat

    if quality.use_llm:
        try:
            embed_text, llm_score = _llm_summary(flat)
            is_summary = True
            time.sleep(LLM_CALL_DELAY)
            log.info("  LLM summary done. LLM quality score: %s/5", llm_score)
        except Exception as exc:
            log.warning("  LLM failed — using flat text. Error: %s", exc)

    # -- Embed + similarity check ---------------------------------------------
    vector     = embed_document(embed_text)
    near_dupes: list[NearDuplicate] = check_near_duplicates(vector, own_point_id=point_id)
    if near_dupes:
        summary.near_dupes_logged += len(near_dupes)

    # -- Title from subject or first line -------------------------------------
    root    = messages[0]
    subject = root.subject or ""
    if not subject and root.id in cleaned_map:
        subject = cleaned_map[root.id].split("\n")[0][:80]
    title   = f"Teams — #{channel_name} — {subject or thread_id}"

    last_msg      = trimmed[-1]
    participants  = sorted({m.sender for m in user_messages})

    # -- Payload with quality fields ------------------------------------------
    payload = TeamsPayload(
        source_uri    = f"https://teams.microsoft.com/l/message/{channel_id}/{thread_id}",
        title         = title,
        author        = root.sender,
        date          = root.createdDateTime.strftime("%Y-%m-%d"),
        content       = embed_text,
        content_hash  = revision,
        is_summary    = is_summary,
        thread_id     = thread_id,
        channel_name  = channel_name,
        participants  = participants,
        message_count = len(user_messages),
        last_reply_at = last_msg.createdDateTime.isoformat(),
    )

    payload_dict = payload.model_dump(mode="json")
    payload_dict["quality_score"]   = quality.score
    payload_dict["quality_tier"]    = quality.tier
    payload_dict["quality_reasons"] = quality.reasons
    if llm_score is not None:
        payload_dict["llm_quality_score"] = llm_score

    from qdrant_client.models import PointStruct
    from app.services.qdrant_store import get_client as get_qdrant
    get_qdrant().upsert(
        collection_name=settings.qdrant_collection,
        points=[PointStruct(id=point_id, vector=vector, payload=payload_dict)],
    )

    if quality.tier == "high":
        summary.ingested_high += 1
    elif quality.tier == "medium":
        summary.ingested_medium += 1
    else:
        summary.ingested_low += 1

    return IngestionResult(
        status          = IngestStatus.ingested,
        point_id        = point_id,
        message         = f"Thread ingested (tier={quality.tier} score={quality.score:.2f})",
        near_duplicates = near_dupes,
    )


# =============================================================================
# Batch entry point
# =============================================================================

def ingest_teams_batch(
    threads: list[TeamsBatchThread],
) -> TeamsBatchSummary:
    """
    Mass-ingest a list of Teams channel threads.

    Parameters
    ----------
    threads : list[TeamsBatchThread]
        Each entry must have raw_messages (root first, replies after),
        thread_id, channel_id, channel_name, team_id.
        Pre-filter to threads with >1 reply before calling — the gate will
        catch them but it's more efficient to filter early.

    Returns
    -------
    TeamsBatchSummary
    """
    total   = len(threads)
    summary = TeamsBatchSummary(total=total)

    log.info("Teams batch ingest start: %d threads", total)

    for i, thread in enumerate(threads, start=1):
        result = _process_one(thread, summary, i, total)
        summary.results.append(result)

    summary.log_summary()
    return summary
