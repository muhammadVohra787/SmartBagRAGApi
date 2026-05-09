"""
Raw API models for Azure DevOps.
Mirrors the shape returned by:
  GET /{org}/{project}/_apis/wit/workitems/{id}?$expand=all&api-version=7.1
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ADOIdentityRef(BaseModel):
    """Identity reference used in ADO for assignees, creators, commenters."""
    model_config = {"extra": "ignore"}

    id: str | None = None
    displayName: str | None = None
    uniqueName: str | None = None

    @property
    def name(self) -> str:
        return self.displayName or self.uniqueName or "Unknown"


class ADOWorkItemFields(BaseModel):
    """
    Typed wrapper around the ADO work item 'fields' dict.
    All ADO field keys use dot-notation ('System.Title') which Pydantic
    cannot map directly, so we parse from a raw dict via from_raw().
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
    rev: int
    url: str                                   = ""
    fields: dict[str, Any]                     = Field(default_factory=dict)
    comments: dict[str, Any]                   = Field(default_factory=dict)

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
        """
        Construct the work item URL for citation.
        org should be the full ADO organization URL (e.g., https://dev.azure.com/your-org)
        """
        return f"{org}/{project}/_workitems/edit/{self.id}"
