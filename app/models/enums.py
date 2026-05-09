"""
Shared enums for the ingestion system.
"""

from enum import Enum


class SourceType(str, Enum):
    pdf      = "pdf"
    markdown = "markdown"
    ado      = "ado_work_item"
    teams    = "teams_thread"


class IngestStatus(str, Enum):
    ingested = "ingested"
    skipped  = "skipped"   # content unchanged — nothing to do
    error    = "error"
