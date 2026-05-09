"""
Qdrant payload models — stored alongside vectors in Qdrant.
All payloads extend BasePayload with source-specific fields.
"""

from pydantic import BaseModel, Field

from app.models.constants import EMBEDDING_DIMS, EMBEDDING_MODEL_NAME, EMBEDDING_VERSION, SOURCE_PRIORITY
from app.models.enums import SourceType


class BasePayload(BaseModel):
    """
    Fields present on every document regardless of source.
    Serialise with .model_dump() before sending to Qdrant.
    """
    source_type:       SourceType
    source_uri:        str
    title:             str
    author:            str | None         = None
    date:              str | None         = None
    content:           str
    content_hash:      str
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
    participants:      list[str]          = Field(default_factory=list)


class TeamsPayload(BasePayload):
    source_type:       SourceType         = SourceType.teams
    source_priority:   int                = SOURCE_PRIORITY[SourceType.teams]
    thread_id:         str
    channel_name:      str
    participants:      list[str]          = Field(default_factory=list)
    message_count:     int                = 0
    last_reply_at:     str | None         = None


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
