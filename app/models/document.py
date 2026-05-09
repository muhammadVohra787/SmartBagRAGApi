"""Document model definitions."""

from dataclasses import dataclass


@dataclass
class Document:
    id: str
    title: str
    content: str
    metadata: dict
