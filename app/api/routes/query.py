"""
Query endpoints for searching the knowledge base.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.embedding_service import embed_document
from app.stores.qdrant_store import search, CONFIDENCE_THRESHOLD
from app.services.llm_service import synthesize_answer

router = APIRouter(prefix="/query", tags=["query"])


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    source_type: str | None = None


class SearchResult(BaseModel):
    title: str
    source_type: str
    source_uri: str
    content_snippet: str
    score: float


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    confidence_met: bool


class Source(BaseModel):
    title: str
    source_type: str
    source_uri: str


class AnswerResponse(BaseModel):
    query: str
    answer: str
    sources: list[Source]
    confidence_met: bool


@router.post("/search", response_model=SearchResponse)
def search_knowledge_base(request: SearchRequest):
    """
    Search the knowledge base and return raw retrieval results.
    No LLM involved - just vector similarity search.
    """
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    # Embed the query
    query_vector = embed_document(request.query)

    # Search Qdrant
    results = search(
        query_vector=query_vector,
        top_k=request.top_k,
        source_type=request.source_type,
    )

    # Check confidence threshold
    confidence_met = bool(results) and results[0].score >= CONFIDENCE_THRESHOLD

    # Format results
    search_results = []
    for result in results:
        payload = result.payload or {}
        content = payload.get("content", "")

        search_results.append(
            SearchResult(
                title=payload.get("title", "Untitled"),
                source_type=payload.get("source_type", "unknown"),
                source_uri=payload.get("source_uri", ""),
                content_snippet=content,
                score=result.score,
            )
        )

    return SearchResponse(
        query=request.query,
        results=search_results,
        confidence_met=confidence_met,
    )


@router.post("/answer", response_model=AnswerResponse)
def answer_question(request: SearchRequest):
    """
    Answer a question using LLM synthesis over retrieved documents.
    Performs the same search as /search, then passes results to LLM for synthesis.
    """
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    # Embed the query
    query_vector = embed_document(request.query)

    # Search Qdrant
    results = search(
        query_vector=query_vector,
        top_k=request.top_k,
        source_type=request.source_type,
    )

    # Check confidence threshold
    confidence_met = bool(results) and results[0].score >= CONFIDENCE_THRESHOLD

    if not confidence_met:
        return AnswerResponse(
            query=request.query,
            answer="I don't have enough relevant information to answer this question confidently.",
            sources=[],
            confidence_met=False,
        )

    # Prepare documents for LLM synthesis (top 5)
    documents = []
    sources = []
    for result in results[:5]:
        payload = result.payload or {}
        documents.append({
            "title": payload.get("title", "Untitled"),
            "source_type": payload.get("source_type", "unknown"),
            "content": payload.get("content", ""),
        })
        sources.append(
            Source(
                title=payload.get("title", "Untitled"),
                source_type=payload.get("source_type", "unknown"),
                source_uri=payload.get("source_uri", ""),
            )
        )

    # Synthesize answer with LLM
    try:
        answer = synthesize_answer(request.query, documents)
    except Exception as e:
        # If LLM fails, return error message
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate answer: {str(e)}"
        )

    return AnswerResponse(
        query=request.query,
        answer=answer,
        sources=sources,
        confidence_met=True,
    )
