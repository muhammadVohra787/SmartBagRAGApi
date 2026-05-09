"""
Raw API models for MS Teams / Microsoft Graph.
Mirrors the shape returned by:
  GET /teams/{teamId}/channels/{channelId}/messages/{messageId}/replies
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
    model_config = {"populate_by_name": True, "extra": "ignore"}

    id: str
    createdDateTime: datetime
    lastModifiedDateTime: datetime | None = None
    messageType: str = "message"
    subject: str | None = None
    body: GraphMessageBody = Field(default_factory=GraphMessageBody)
    from_: GraphFrom | None = Field(None, alias="from")
    replyToId: str | None = None

    @property
    def sender(self) -> str:
        return self.from_.display_name if self.from_ else "Unknown"

    @property
    def is_user_message(self) -> bool:
        return self.messageType == "message"


class TeamsThreadRaw(BaseModel):
    """
    Everything the pipeline needs about a thread, as received from the caller.
    The caller (bot invoke handler or test) assembles this after fetching from Graph.
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
