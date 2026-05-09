"""Metadata model definitions."""

from dataclasses import dataclass


@dataclass
class Metadata:
    source: str
    author: str | None = None
    created_at: str | None = None
