"""Search model definitions."""

from dataclasses import dataclass


@dataclass
class SearchResult:
    document_id: str
    score: float
    snippet: str
