"""
services/embedder.py

Wraps sentence-transformers so the rest of the codebase never touches the model directly.
Loaded once at startup via the module-level singleton.

BGE models use an asymmetric retrieval scheme:
  - Documents at index time:  prefix "Represent this sentence: "
  - Queries at search time:   prefix "Represent this question: "
This matters for retrieval quality — do not skip the prefixes.
"""

from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.core.settings import settings
from app.models.models import EMBEDDING_DIMS


@lru_cache(maxsize=1)
def _load_model() -> SentenceTransformer:
    """
    Load and cache the embedding model.
    lru_cache ensures this runs exactly once per process even if called from
    multiple modules. The model stays in memory for the lifetime of the worker.
    """
    model = SentenceTransformer(
        settings.embedding_model,
        device=settings.embedding_device,
    )
    actual_dims = model.get_sentence_embedding_dimension()
    if actual_dims != EMBEDDING_DIMS:
        raise RuntimeError(
            f"Loaded model '{settings.embedding_model}' produces {actual_dims}-dim vectors "
            f"but the collection expects {EMBEDDING_DIMS}. "
            f"Check EMBEDDING_MODEL in your .env file."
        )
    return model


def embed_document(text: str) -> list[float]:
    """
    Embed a document chunk for storage.
    BGE document prefix applied automatically.
    Returns a normalised 768-dim float list ready for Qdrant upsert.
    """
    model = _load_model()
    vector = model.encode(
        f"Represent this sentence: {text}",
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vector.tolist()


def embed_query(text: str) -> list[float]:
    """
    Embed a user query for search.
    BGE query prefix applied automatically.
    """
    model = _load_model()
    vector = model.encode(
        f"Represent this question: {text}",
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vector.tolist()
