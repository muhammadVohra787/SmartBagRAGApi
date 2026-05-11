"""Mass ingestion pipelines with quality gates and LLM processing."""

from app.ingestion.mass.ado_batch import ingest_ado_batch, ADOBatchSummary
from app.ingestion.mass.teams_batch import ingest_teams_batch, TeamsBatchSummary
from app.ingestion.mass.quality import (
    score_ado_item,
    score_teams_thread,
    ADOQualityResult,
    TeamsQualityResult,
)
from app.ingestion.mass.prompts import (
    parse_ado_response,
    parse_teams_response,
    format_ado_summary,
    format_teams_summary,
    ADO_BUG_SYSTEM,
    ADO_BUG_USER,
    TEAMS_THREAD_SYSTEM,
    TEAMS_THREAD_USER,
)
from app.ingestion.mass.keywords import (
    ADO_DISQUALIFY,
    ADO_NOISE,
    TEAMS_DISQUALIFY,
    TEAMS_NOISE,
    check_keywords,
    contains_disqualifier,
)

__all__ = [
    "ingest_ado_batch",
    "ingest_teams_batch",
    "ADOBatchSummary",
    "TeamsBatchSummary",
    "score_ado_item",
    "score_teams_thread",
    "ADOQualityResult",
    "TeamsQualityResult",
    "parse_ado_response",
    "parse_teams_response",
    "format_ado_summary",
    "format_teams_summary",
    "ADO_BUG_SYSTEM",
    "ADO_BUG_USER",
    "TEAMS_THREAD_SYSTEM",
    "TEAMS_THREAD_USER",
    "ADO_DISQUALIFY",
    "ADO_NOISE",
    "TEAMS_DISQUALIFY",
    "TEAMS_NOISE",
    "check_keywords",
    "contains_disqualifier",
]
