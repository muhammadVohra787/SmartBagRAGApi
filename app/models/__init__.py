"""
Models package — unified exports for all model types.
"""

from app.models.ado import ADOComment, ADOIdentityRef, ADOWorkItemFields, ADOWorkItemRaw
from app.models.constants import (
    EMBEDDING_DIMS,
    EMBEDDING_MODEL_NAME,
    EMBEDDING_VERSION,
    SIMILARITY_DUPE_THRESHOLD,
    SOURCE_PRIORITY,
)
from app.models.enums import IngestStatus, SourceType
from app.models.graph import GraphFrom, GraphMessage, GraphMessageBody, GraphUserIdentity, TeamsThreadRaw
from app.models.payloads import ADOPayload, BasePayload, MarkdownPayload, PDFPayload, TeamsPayload
from app.models.results import BatchIngestionResult, IngestionResult, NearDuplicate, SearchResponse, SearchResult
from app.models.schemas import IngestADORequest, IngestTeamsRequest

__all__ = [
    # Enums
    "SourceType",
    "IngestStatus",
    # Constants
    "EMBEDDING_MODEL_NAME",
    "EMBEDDING_VERSION",
    "EMBEDDING_DIMS",
    "SIMILARITY_DUPE_THRESHOLD",
    "SOURCE_PRIORITY",
    # Graph models (Teams/MS Graph)
    "GraphUserIdentity",
    "GraphFrom",
    "GraphMessageBody",
    "GraphMessage",
    "TeamsThreadRaw",
    # ADO models
    "ADOIdentityRef",
    "ADOWorkItemFields",
    "ADOComment",
    "ADOWorkItemRaw",
    # Payloads
    "BasePayload",
    "ADOPayload",
    "TeamsPayload",
    "PDFPayload",
    "MarkdownPayload",
    # Results
    "NearDuplicate",
    "IngestionResult",
    "BatchIngestionResult",
    "SearchResult",
    "SearchResponse",
    # Schemas
    "IngestADORequest",
    "IngestTeamsRequest",
]
