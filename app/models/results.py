"""
Pipeline result and API response models.
"""

from pydantic import BaseModel, Field

from app.models.enums import IngestStatus, SourceType


class NearDuplicate(BaseModel):
    """Logged when a different document scores above SIMILARITY_DUPE_THRESHOLD."""
    existing_point_id: str
    score:             float
    existing_title:    str | None = None


class IngestionResult(BaseModel):
    """Returned by single-document ingestion functions (ADO, Teams)."""
    status:          IngestStatus
    point_id:        str | None              = None
    message:         str                     = ""
    near_duplicates: list[NearDuplicate]     = Field(default_factory=list)


class BatchIngestionResult(BaseModel):
    """Returned by batch ingestion functions (PDF, Markdown)."""
    file_path:       str
    status:          IngestStatus
    chunks_ingested: int                     = 0
    chunks_skipped:  int                     = 0
    message:         str                     = ""
    near_duplicates: list[NearDuplicate]     = Field(default_factory=list)


class SearchResult(BaseModel):
    """One entry in a /query/search response."""
    point_id:    str
    score:       float
    title:       str
    source_type: SourceType
    source_uri:  str
    snippet:     str
    author:      str | None = None
    date:        str | None = None


class SearchResponse(BaseModel):
    """Response from /query/search endpoint."""
    results:            list[SearchResult]
    query:              str
    confidence_too_low: bool = False
