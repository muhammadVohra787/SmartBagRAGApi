"""
ingestion/teams.py

MS Teams thread ingestion pipeline.

Entry point
-----------
    ingest_teams_thread(
        raw_messages  : list[dict],    # list of Graph message objects, root first
        thread_id     : str,           # root message ID
        channel_id    : str,
        channel_name  : str,
        team_id       : str,
    ) -> IngestionResult

What it does
------------
1.  Parse the raw Graph API message list into typed models.
2.  Filter out system messages (join/leave, call events, etc.).
3.  Sanitise each message body from HTML to plain text using html2text.
4.  Discard messages that are empty or under 10 words after cleaning.
5.  Build the revision marker: message_count + ISO timestamp of the last reply.
6.  Stage 1 dedup — compare against what Qdrant has stored.
    If unchanged, return skipped.
7.  Flatten all cleaned messages into a single chronological text block.
8.  Token-count the block (tiktoken). Trim from the oldest messages if
    the thread exceeds ~12,000 tokens to fit the LLM context window.
9.  Send to Azure OpenAI for structured summarisation.
10. If the LLM fails, fall back to storing the cleaned raw text (is_summary=False).
    The document is never dropped — a raw thread is better than nothing.
11. Embed the summary (or fallback text).
12. Stage 2 similarity check — log near-duplicates from different IDs.
13. Upsert with full citation + embedding metadata.
14. Return IngestionResult.

Called by
---------
POST /ingest/teams  (FastAPI route)
The bot invoke handler (message action) fetches messages from Graph,
assembles a TeamsThreadRaw, and calls this function.
"""

import logging

import html2text
import tiktoken

from app.models import (
    GraphMessage,
    IngestStatus,
    IngestionResult,
    NearDuplicate,
    TeamsPayload,
)
from app.services.embedding_service import embed_document
from app.stores.qdrant_store import check_near_duplicates, get_payload_by_id, upsert_point

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TOKENS          = 12_000    # trim thread text to this before LLM call
MIN_MESSAGE_WORDS   = 10        # discard messages shorter than this after cleaning
TIKTOKEN_ENCODING   = "cl100k_base"   # matches gpt-4 / gpt-4o tokenizer

# ---------------------------------------------------------------------------
# html2text configuration
# ---------------------------------------------------------------------------

_h2t = html2text.HTML2Text()
_h2t.ignore_links      = True    # drop URLs, keep visible link text
_h2t.ignore_images     = True    # drop <img> entirely
_h2t.ignore_emphasis   = True    # flatten *bold* and _italic_ to plain text
_h2t.body_width        = 0       # no line-wrapping (0 = unlimited)
_h2t.unicode_snob      = True    # preserve unicode characters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _point_id(thread_id: str) -> str:
    return f"teams::{thread_id}"


def _sanitise_html(html_content: str) -> str:
    """Convert a Teams message HTML body to clean plain text."""
    return _h2t.handle(html_content).strip()


def _clean_message(msg: GraphMessage) -> str | None:
    """
    Return cleaned plain text for a message, or None if it should be discarded.
    Discarded if:
      - not a user message (system events)
      - body is empty after HTML stripping
      - fewer than MIN_MESSAGE_WORDS words after stripping
    """
    if not msg.is_user_message:
        return None

    raw_body = msg.body.content or ""
    if not raw_body.strip():
        return None

    if msg.body.contentType == "html":
        text = _sanitise_html(raw_body)
    else:
        text = raw_body.strip()

    if not text:
        return None

    if len(text.split()) < MIN_MESSAGE_WORDS:
        log.debug(
            "Message %s discarded (too short after cleaning: %d words)",
            msg.id, len(text.split()),
        )
        return None

    return text


def _flatten_thread(messages: list[GraphMessage], cleaned_map: dict[str, str]) -> str:
    """
    Assemble the cleaned messages into a chronological text block.
    Format per message:  [YYYY-MM-DD] Sender Name: message text
    """
    lines = []
    for msg in messages:
        text = cleaned_map.get(msg.id)
        if text is None:
            continue
        date_str = msg.createdDateTime.strftime("%Y-%m-%d")
        lines.append(f"[{date_str}] {msg.sender}: {text}")
    return "\n\n".join(lines)


def _count_tokens(text: str) -> int:
    enc = tiktoken.get_encoding(TIKTOKEN_ENCODING)
    return len(enc.encode(text))


def _trim_to_token_limit(
    messages: list[GraphMessage],
    cleaned_map: dict[str, str],
    max_tokens: int,
) -> list[GraphMessage]:
    """
    Remove the oldest messages from the list until the flattened thread
    fits within max_tokens. Always keeps the root message (index 0).
    Returns the trimmed message list.
    """
    trimmed = list(messages)
    while len(trimmed) > 1:
        candidate = _flatten_thread(trimmed, cleaned_map)
        if _count_tokens(candidate) <= max_tokens:
            break
        # Remove the oldest reply (index 1 — keep root message at 0)
        removed = trimmed.pop(1)
        log.debug("Thread trimmed: removed message %s to fit token limit", removed.id)
    return trimmed


def _revision_marker(messages: list[GraphMessage], user_message_count: int) -> str:
    """
    Build a string that changes whenever the thread has new content.
    Format: "{user_message_count}::{last_modified_iso}"
    """
    last_ts = max(
        (m.lastModifiedDateTime or m.createdDateTime for m in messages),
        default=messages[-1].createdDateTime,
    )
    return f"{user_message_count}::{last_ts.isoformat()}"


# ---------------------------------------------------------------------------
# LLM summarisation
# ---------------------------------------------------------------------------

_TEAMS_SYSTEM = (
    "You are a technical knowledge base assistant for a software engineering team. "
    "Extract structured, factual information from internal MS Teams threads "
    "so it can be searched and retrieved later. Be concise and specific."
)

_TEAMS_USER = """\
Summarise the following Teams thread. Return exactly these five sections:

1. Topic — one sentence describing the subject of the thread.
2. Key Decisions — bullet list of any decisions made or conclusions reached. If none, write "None".
3. Action Items — bullet list of tasks assigned or follow-ups mentioned. If none, write "None".
4. Technical Details — config values, system names, API names, version numbers, or code-level specifics mentioned.
5. Participants — comma-separated list of people who contributed meaningfully.

Thread:
{thread_text}
"""


def _summarise(thread_text: str) -> str:
    """
    Call Azure OpenAI. Raises on failure so the caller can fall back.
    """
    from app.services.llm_service import get_client
    from app.core.settings import settings

    client   = get_client()
    response = client.chat.completions.create(
        model=settings.azure_openai_deployment_name,
        max_tokens=800,
        temperature=0.2,
        messages=[
            {"role": "system", "content": _TEAMS_SYSTEM},
            {"role": "user",   "content": _TEAMS_USER.format(thread_text=thread_text)},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM returned empty summary")
    return content.strip()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ingest_teams_thread(
    raw_messages: list[dict],
    thread_id:    str,
    channel_id:   str,
    channel_name: str,
    team_id:      str,
) -> IngestionResult:
    """
    Ingest a Teams channel thread into Qdrant.

    Parameters
    ----------
    raw_messages : list[dict]
        The full list of Graph message objects — root message first, then replies
        in chronological order. Each dict is the raw JSON from the Graph API.

    thread_id : str
        The root message ID. Used as the Qdrant point key.

    channel_id : str
        Graph channel ID (for citation metadata).

    channel_name : str
        Human-readable channel name (for citation metadata).

    team_id : str
        Graph team ID (for citation metadata).

    Returns
    -------
    IngestionResult
    """
    point_id = _point_id(thread_id)
    log.info("Teams ingest start: thread_id=%s channel=%s", thread_id, channel_name)

    # ------------------------------------------------------------------
    # Parse raw Graph messages into typed models
    # ------------------------------------------------------------------
    try:
        messages: list[GraphMessage] = [
            GraphMessage.model_validate(m) for m in raw_messages
        ]
    except Exception as exc:
        return IngestionResult(
            status=IngestStatus.error,
            message=f"Failed to parse Graph messages: {exc}",
        )

    if not messages:
        return IngestionResult(
            status=IngestStatus.error,
            message="No messages provided.",
        )

    # ------------------------------------------------------------------
    # Clean each message — filter system events, sanitise HTML, discard short
    # ------------------------------------------------------------------
    cleaned_map: dict[str, str] = {}   # message.id → cleaned text
    participants: set[str] = set()

    for msg in messages:
        text = _clean_message(msg)
        if text is None:
            continue
        cleaned_map[msg.id] = text
        participants.add(msg.sender)

    user_messages = [m for m in messages if m.id in cleaned_map]

    if not user_messages:
        return IngestionResult(
            status=IngestStatus.error,
            message="No usable user messages after cleaning.",
        )

    # ------------------------------------------------------------------
    # Stage 1 — revision dedup
    # ------------------------------------------------------------------
    revision = _revision_marker(messages, len(user_messages))
    existing = get_payload_by_id(point_id)

    if existing:
        stored_hash = existing.get("content_hash", "")
        if stored_hash == revision:
            log.info("Teams skipped (unchanged): thread_id=%s", thread_id)
            return IngestionResult(
                status=IngestStatus.skipped,
                point_id=point_id,
                message="Thread has not changed since last ingest.",
            )

    # ------------------------------------------------------------------
    # Trim to token limit (keep most recent messages)
    # ------------------------------------------------------------------
    trimmed_messages = _trim_to_token_limit(user_messages, cleaned_map, MAX_TOKENS)
    flat_text = _flatten_thread(trimmed_messages, cleaned_map)

    if len(trimmed_messages) < len(user_messages):
        log.info(
            "Thread trimmed from %d to %d messages to fit token limit (thread_id=%s)",
            len(user_messages), len(trimmed_messages), thread_id,
        )

    # ------------------------------------------------------------------
    # LLM summarisation (with fallback to raw text)
    # ------------------------------------------------------------------
    is_summary = False
    embed_text = flat_text

    try:
        embed_text = _summarise(flat_text)
        is_summary = True
        log.info("Teams LLM summary generated for thread_id=%s", thread_id)
    except Exception as exc:
        log.warning(
            "Teams LLM summary failed for thread_id=%s — storing raw text instead. Error: %s",
            thread_id, exc,
        )
        # embed_text already = flat_text, is_summary already False

    # ------------------------------------------------------------------
    # Embed
    # ------------------------------------------------------------------
    vector = embed_document(embed_text)

    # ------------------------------------------------------------------
    # Stage 2 — similarity check
    # ------------------------------------------------------------------
    near_dupes: list[NearDuplicate] = check_near_duplicates(vector, own_point_id=point_id)

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    root_msg      = messages[0]
    last_msg      = trimmed_messages[-1]
    created_date  = root_msg.createdDateTime.strftime("%Y-%m-%d")
    last_reply_at = last_msg.createdDateTime.isoformat()

    # Derive a title from the thread subject or first meaningful line
    subject = root_msg.subject or ""
    if not subject and root_msg.id in cleaned_map:
        first_line = cleaned_map[root_msg.id].split("\n")[0]
        subject = first_line[:80]
    title = f"Teams — #{channel_name} — {subject or thread_id}"

    # ------------------------------------------------------------------
    # Build payload
    # ------------------------------------------------------------------
    payload = TeamsPayload(
        source_uri      = f"https://teams.microsoft.com/l/message/{channel_id}/{thread_id}",
        title           = title,
        author          = root_msg.sender,
        date            = created_date,
        content         = embed_text,
        content_hash    = revision,
        is_summary      = is_summary,
        thread_id       = thread_id,
        channel_name    = channel_name,
        participants    = sorted(participants),
        message_count   = len(user_messages),
        last_reply_at   = last_reply_at,
    )

    # ------------------------------------------------------------------
    # Upsert — overwrites any existing entry with the same point_id
    # ------------------------------------------------------------------
    upsert_point(point_id=point_id, vector=vector, payload=payload)

    log.info(
        "Teams ingest complete: thread_id=%s messages=%d near_dupes=%d is_summary=%s",
        thread_id, len(user_messages), len(near_dupes), is_summary,
    )

    return IngestionResult(
        status          = IngestStatus.ingested,
        point_id        = point_id,
        message         = f"Ingested thread with {len(user_messages)} messages.",
        near_duplicates = near_dupes,
    )
