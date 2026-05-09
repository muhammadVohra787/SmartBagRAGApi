"""
Embedding model constants and source prioritization.
Change these in one place when upgrading the model or changing priorities.
"""

from app.models.enums import SourceType

EMBEDDING_MODEL_NAME      = "bge-base-en-v1.5"
EMBEDDING_VERSION         = 1
EMBEDDING_DIMS            = 768
SIMILARITY_DUPE_THRESHOLD = 0.93

SOURCE_PRIORITY: dict[SourceType, int] = {
    SourceType.pdf:      1,
    SourceType.markdown: 1,
    SourceType.ado:      2,
    SourceType.teams:    3,
}
