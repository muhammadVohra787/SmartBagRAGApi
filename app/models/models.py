"""
models.py

Pydantic models for all four ingestion sources and their Qdrant payloads.

Structure
---------
Raw API models    — what comes directly from the external API (MS Graph, ADO REST).
                    These are typed mirrors of the raw JSON so pipelines are not
                    littered with dict key lookups.

Payload models    — what gets stored in Qdrant alongside the vector.
                    Every source shares BasePayload, then extends it.

Result models     — what ingestion functions return to the caller.

Request/Response  — FastAPI route schemas (ingest endpoints, query endpoints).
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Enums
# =============================================================================

class SourceType(str, Enum):
    pdf         = "pdf"
    markdown    = "markdown"
    ado         = "ado_work_item"
    teams       = "teams_thread"


class IngestStatus(str, Enum):
    ingested    = "ingested"
    skipped     = "skipped"     # content hash unchanged — nothing to do
    error       = "error"


# =============================================================================
# Embedding / versioning constants
# Centralised here so a model upgrade is one change in one place.
# =============================================================================

EMBEDDING_MODEL_NAME    = "bge-base-en-v1.5"
EMBEDDING_VERSION       = 1
EMBEDDING_DIMS          = 768
SIMILARITY_DUPE_THRESHOLD = 0.93   # log warning if different doc scores above this

SOURCE_PRIORITY: dict[SourceType, int] = {
    SourceType.pdf:      1,
    SourceType.markdown: 1,
    SourceType.ado:      2,
    SourceType.teams:    3,
}


# =============================================================================
# Raw API models — MS Teams (Microsoft Graph)
# Mirrors the shape returned by:
#   GET /teams/{teamId}/channels/{channelId}/messages/{messageId}/replies
# =============================================================================

class GraphUserIdentity(BaseModel):
    """The 'user' sub-object inside a Graph 'from' or 'createdBy' field."""
    id: str | None = None
    displayName: str | None = None


class GraphFrom(BaseModel):
    """The 'from' field on a Graph channel message."""
    user: GraphUserIdentity | None = None

    @property
    def display_name(self) -> str:
        return (self.user and self.user.displayName) or "Unknown"


class GraphMessageBody(BaseModel):
    contentType: Literal["text", "html"] = "html"
    content: str = ""


class GraphMessage(BaseModel):
    """
    A single message or reply from the MS Graph channel messages API.
    Fields not listed are silently ignored (extra="ignore").
    """
    model_config = {"extra": "ignore"}

    id: str
    createdDateTime: datetime
    lastModifiedDateTime: datetime | None = None
    messageType: str = "message"          # "message" | "systemEventMessage" | etc.
    subject: str | None = None
    body: GraphMessageBody = Field(default_factory=GraphMessageBody)
    from_: GraphFrom | None = Field(None, alias="from")
    replyToId: str | None = None          # None = root message, str = reply

    @property
    def sender(self) -> str:
        return self.from_.display_name if self.from_ else "Unknown"

    @property
    def is_user_message(self) -> bool:
        return self.messageType == "message"

    model_config = {"populate_by_name": True, "extra": "ignore"}


class TeamsThreadRaw(BaseModel):
    """
    Everything the pipeline needs about a thread, as received from the caller.
    The caller (bot invoke handler or test) assembles this after fetching from Graph.

    messages        — root message first, replies after, in chronological order
    thread_id       — the root message ID (used as the Qdrant point ID key)
    channel_id      — Graph channel ID
    channel_name    — human-readable channel name for citation metadata
    team_id         — Graph team ID
    """
    messages:       list[GraphMessage]
    thread_id:      str
    channel_id:     str
    channel_name:   str
    team_id:        str

    @field_validator("messages")
    @classmethod
    def must_have_messages(cls, v: list) -> list:
        if not v:
            raise ValueError("Thread must contain at least one message")
        return v


# =============================================================================
# Raw API models — Azure DevOps (REST API)
# Mirrors the shape returned by:
#   GET /{org}/{project}/_apis/wit/workitems/{id}?$expand=all&api-version=7.1
# =============================================================================

class ADOIdentityRef(BaseModel):
    """Identity reference used in ADO for assignees, creators, commenters."""
    model_config = {"extra": "ignore"}

    id: str | None = None
    displayName: str | None = None
    uniqueName: str | None = None   # usually the email address

    @property
    def name(self) -> str:
        return self.displayName or self.uniqueName or "Unknown"


class ADOWorkItemFields(BaseModel):
    """
    Typed wrapper around the ADO work item 'fields' dict.
    All ADO field keys use dot-notation ('System.Title') which Pydantic
    cannot map directly, so we parse from a raw dict via from_fields().
    """
    title: str
    work_item_type: str
    state: str
    area_path: str           = ""
    team_project: str        = ""
    description: str         = ""
    acceptance_criteria: str = ""
    tags: list[str]          = Field(default_factory=list)
    assigned_to: str         = "Unassigned"
    created_by: str          = "Unknown"
    created_date: datetime | None = None
    changed_date: datetime | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "ADOWorkItemFields":
        """Parse the raw 'fields' dict from the ADO REST response."""

        def _name(field_val: Any) -> str:
            """Extract displayName from an identity ref dict or return the value as-is."""
            if isinstance(field_val, dict):
                return field_val.get("displayName") or field_val.get("uniqueName") or "Unknown"
            return str(field_val) if field_val else "Unknown"

        def _tags(field_val: str | None) -> list[str]:
            if not field_val:
                return []
            return [t.strip() for t in field_val.split(";") if t.strip()]

        def _strip_html(value: str | None) -> str:
            """ADO description and AC fields often contain HTML."""
            if not value:
                return ""
            clean = re.sub(r"<[^>]+>", " ", value)
            return re.sub(r"\s+", " ", clean).strip()

        return cls(
            title              = raw.get("System.Title", "Untitled"),
            work_item_type     = raw.get("System.WorkItemType", "Unknown"),
            state              = raw.get("System.State", "Unknown"),
            area_path          = raw.get("System.AreaPath", ""),
            team_project       = raw.get("System.TeamProject", ""),
            description        = _strip_html(raw.get("System.Description")),
            acceptance_criteria= _strip_html(raw.get("Microsoft.VSTS.Common.AcceptanceCriteria")),
            tags               = _tags(raw.get("System.Tags")),
            assigned_to        = _name(raw.get("System.AssignedTo")),
            created_by         = _name(raw.get("System.CreatedBy")),
            created_date       = raw.get("System.CreatedDate"),
            changed_date       = raw.get("System.ChangedDate"),
        )


class ADOComment(BaseModel):
    model_config = {"extra": "ignore"}

    text: str                        = ""
    createdDate: datetime | None     = None
    createdBy: dict[str, Any]        = Field(default_factory=dict)

    @property
    def author(self) -> str:
        return self.createdBy.get("displayName") or "Unknown"

    @property
    def date_str(self) -> str:
        return self.createdDate.strftime("%Y-%m-%d") if self.createdDate else "Unknown date"


class ADOWorkItemRaw(BaseModel):
    """
    The full ADO work item as returned by the REST API with $expand=all.
    The pipeline receives this directly and does not need to know the HTTP layer.
    """
    model_config = {"extra": "ignore"}

    id: int
    rev: int                                   # ADO revision number — used for dedup
    url: str                                   = ""
    fields: dict[str, Any]                     = Field(default_factory=dict)
    comments: dict[str, Any]                   = Field(default_factory=dict)  # { "value": [...] }

    @property
    def parsed_fields(self) -> ADOWorkItemFields:
        return ADOWorkItemFields.from_raw(self.fields)

    @property
    def parsed_comments(self) -> list[ADOComment]:
        raw_comments = self.comments.get("value", [])
        return [ADOComment.model_validate(c) for c in raw_comments]

    @property
    def last_five_comments(self) -> list[ADOComment]:
        return self.parsed_comments[-5:]

    def browser_url(self, org: str, project: str) -> str:
        """Construct the work item URL for citation."""
        return f"{org}/{project}/_workitems/edit/{self.id}"


# =============================================================================
# Qdrant Payload models
# These are serialised to dict and stored as the Qdrant point payload.
# =============================================================================

class BasePayload(BaseModel):
    """
    Fields present on every document regardless of source.
    Serialise with .model_dump() before sending to Qdrant.
    """
    source_type:       SourceType
    source_uri:        str                # URL or repo-relative file path
    title:             str
    author:            str | None         = None
    date:              str | None         = None  # ISO 8601 string — Qdrant stores as str
    content:           str                # the actual text that was embedded
    content_hash:      str                # SHA-256 hex — used for dedup
    is_summary:        bool               = False
    embedding_model:   str                = EMBEDDING_MODEL_NAME
    embedding_version: int                = EMBEDDING_VERSION
    source_priority:   int                = 1


class ADOPayload(BasePayload):
    source_type:       SourceType         = SourceType.ado
    source_priority:   int                = SOURCE_PRIORITY[SourceType.ado]
    work_item_id:      int
    work_item_type:    str
    work_item_state:   str
    tags:              list[str]          = Field(default_factory=list)
    participants:      list[str]          = Field(default_factory=list)  # assignee + commenters


class TeamsPayload(BasePayload):
    source_type:       SourceType         = SourceType.teams
    source_priority:   int                = SOURCE_PRIORITY[SourceType.teams]
    thread_id:         str
    channel_name:      str
    participants:      list[str]          = Field(default_factory=list)
    message_count:     int                = 0
    last_reply_at:     str | None         = None   # ISO 8601 — used as revision marker


class PDFPayload(BasePayload):
    source_type:       SourceType         = SourceType.pdf
    source_priority:   int                = SOURCE_PRIORITY[SourceType.pdf]
    filename:          str
    page_number:       int
    total_pages:       int
    chunk_index:       int


class MarkdownPayload(BasePayload):
    source_type:       SourceType         = SourceType.markdown
    source_priority:   int                = SOURCE_PRIORITY[SourceType.markdown]
    filename:          str
    section_heading:   str
    heading_level:     int
    chunk_index:       int


# =============================================================================
# Pipeline result models
# =============================================================================

class NearDuplicate(BaseModel):
    """Logged when a different document scores above SIMILARITY_DUPE_THRESHOLD."""
    existing_point_id: str
    score:             float
    existing_title:    str | None = None


class IngestionResult(BaseModel):
    """Returned by every ingestion function."""
    status:          IngestStatus
    point_id:        str | None         = None   # Qdrant point ID if ingested
    message:         str                = ""
    near_duplicates: list[NearDuplicate]= Field(default_factory=list)


class BatchIngestionResult(BaseModel):
    """Returned by PDF and Markdown batch pipelines (multiple chunks per file)."""
    file_path:         str
    status:            IngestStatus
    chunks_ingested:   int             = 0
    chunks_skipped:    int             = 0
    message:           str             = ""
    near_duplicates:   list[NearDuplicate] = Field(default_factory=list)


# =============================================================================
# FastAPI request / response schemas
# =============================================================================

class IngestADORequest(BaseModel):
    """
    Body POSTed to POST /ingest/ado by the ADO VSIX extension.
    The extension fetches the raw work item itself and sends it along,
    or sends just the ID and lets the backend fetch it.
    """
    raw_work_item: dict[str, Any]    # full ADO REST API response
    org:     str                     # https://dev.azure.com/your-org
    project: str
    use_ai_summary: bool = False


class IngestTeamsRequest(BaseModel):
    """
    Body POSTed to POST /ingest/teams by the bot invoke handler.
    The bot fetches raw messages from Graph and passes them here.
    """
    raw_messages: list[dict[str, Any]]  # list of Graph message objects
    thread_id:    str
    channel_id:   str
    channel_name: str
    team_id:      str


class SearchResult(BaseModel):
    """One entry in a /query/search response."""
    point_id:    str
    score:       float
    title:       str
    source_type: SourceType
    source_uri:  str
    snippet:     str            # first 300 chars of content
    author:      str | None     = None
    date:        str | None     = None


class SearchResponse(BaseModel):
    results:              list[SearchResult]
    query:                str
    confidence_too_low:   bool  = False  # True when top score < 0.65 threshold
