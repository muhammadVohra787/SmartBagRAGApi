"""
Ingestion API routes.

POST /api/ingest/ado     — ingest ADO work item (called by VSIX extension)
POST /api/ingest/teams   — ingest Teams thread (post-MVP, called by message action)
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel

from app.core.settings import settings
from app.models import IngestionResult
from app.pipelines.ingest_ado_pipeline import ingest_ado_work_item

log = logging.getLogger(__name__)
router = APIRouter(prefix="/ingest", tags=["ingestion"])


# ---------------------------------------------------------------------------
# API key validation
# ---------------------------------------------------------------------------

def verify_api_key(x_api_key: str = Header(...)) -> None:
    """Validate API key from request header."""
    if not settings.backend_api_key:
        raise HTTPException(
            status_code=500,
            detail="BACKEND_API_KEY not configured on server",
        )
    if x_api_key != settings.backend_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ---------------------------------------------------------------------------
# ADO Ingestion
# ---------------------------------------------------------------------------

class ADOIngestRequest(BaseModel):
    """Request body for POST /api/ingest/ado"""
    work_item_id:   int
    org:            str              # e.g. "https://dev.azure.com/your-org"
    project:        str
    use_ai_summary: bool = False
    raw_work_item:  dict             # full ADO REST response ($expand=all)


@router.post("/ado")
def ingest_ado(
    req: ADOIngestRequest,
    _: None = Depends(verify_api_key),
) -> IngestionResult:
    """
    Ingest an ADO work item into Qdrant.
    Called by the VSIX extension when user clicks "Register in KB".

    Security:
    - Requires X-Api-Key header matching INGESTION_API_KEY
    - Rate limited by Nginx (configured on the VM)

    Returns:
    - IngestionResult with status: ingested / skipped / error
    """
    log.info(
        "ADO ingest request: work_item_id=%d org=%s project=%s use_ai_summary=%s",
        req.work_item_id,
        req.org,
        req.project,
        req.use_ai_summary,
    )

    try:
        result = ingest_ado_work_item(
            raw_work_item=req.raw_work_item,
            org=req.org,
            project=req.project,
            use_ai_summary=req.use_ai_summary,
        )
        return result
    except Exception as exc:
        log.exception("Unhandled exception during ADO ingestion: %s", exc)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")


# ---------------------------------------------------------------------------
# Teams Ingestion (post-MVP)
# ---------------------------------------------------------------------------

# TODO: POST /api/ingest/teams
#   - Validate bot auth token
#   - Fetch thread from MS Graph
#   - Clean HTML with html2text
#   - Optional LLM summarisation
#   - Call ingest_teams_thread() pipeline
#   - Reply in the thread with confirmation
