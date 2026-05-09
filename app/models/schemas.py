"""
FastAPI request and response schemas for ingestion endpoints.
"""

from typing import Any

from pydantic import BaseModel


class IngestADORequest(BaseModel):
    """
    Body POSTed to POST /ingest/ado by the ADO VSIX extension.
    The extension fetches the raw work item itself and sends it along.
    """
    raw_work_item: dict[str, Any]
    org:           str
    project:       str
    use_ai_summary: bool = False


class IngestTeamsRequest(BaseModel):
    """
    Body POSTed to POST /ingest/teams by the bot invoke handler.
    The bot fetches raw messages from Graph and passes them here.
    """
    raw_messages: list[dict[str, Any]]
    thread_id:    str
    channel_id:   str
    channel_name: str
    team_id:      str
