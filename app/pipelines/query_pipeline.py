"""Pipeline for querying the knowledge base and retrieving relevant documents."""

from app.services.llm_service import LLMService
from app.services.qdrant_service import QdrantService


class QueryPipeline:
    def run(self, query: str) -> None:
        raise NotImplementedError("Implement query pipeline logic")
