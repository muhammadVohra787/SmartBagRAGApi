"""
app/ingestion/mass/quality.py

Heuristic quality scoring for mass ingestion.

Two scorers:
    score_ado_item(fields, comments)         → ADOQualityResult
    score_teams_thread(messages, cleaned)    → TeamsQualityResult

Scoring is purely heuristic — no LLM calls.
The score gates whether the item reaches the LLM at all.

Tiers
-----
    noise   < 0.25  → skip entirely. Not ingested.
    low     0.25–0.44 → ingest with heuristic serialisation only. No LLM.
    medium  0.45–0.69 → ingest with LLM summary.
    high    >= 0.70  → ingest with LLM summary. Marked as priority.

Ingestion decision
------------------
    noise   → skip
    low     → upsert serialised text, quality_tier="low"
    medium  → LLM summary, quality_tier="medium"
    high    → LLM summary, quality_tier="high"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ingestion.mass.keywords import (
    ADO_DISQUALIFY,
    ADO_NOISE,
    TEAMS_DISQUALIFY,
    TEAMS_NOISE,
    check_keywords,
)
from app.models import GraphMessage


# =============================================================================
# Tier boundaries
# =============================================================================

TIER_NOISE  = 0.25
TIER_LOW    = 0.45
TIER_MEDIUM = 0.70


def _to_tier(score: float) -> str:
    if score < TIER_NOISE:  return "noise"
    if score < TIER_LOW:    return "low"
    if score < TIER_MEDIUM: return "medium"
    return "high"


# =============================================================================
# Result models
# =============================================================================

@dataclass
class ADOQualityResult:
    score:             float
    tier:              str            # noise / low / medium / high
    reasons:           list[str]      = field(default_factory=list)
    disqualify_hits:   list[str]      = field(default_factory=list)
    noise_hits:        list[str]      = field(default_factory=list)

    @property
    def should_skip(self) -> bool:
        return self.tier == "noise"

    @property
    def use_llm(self) -> bool:
        return self.tier in ("medium", "high")


@dataclass
class TeamsQualityResult:
    score:             float
    tier:              str
    reasons:           list[str]      = field(default_factory=list)
    disqualify_hits:   list[str]      = field(default_factory=list)
    noise_hits:        list[str]      = field(default_factory=list)
    reply_count:       int            = 0
    unique_participants: int          = 0

    @property
    def should_skip(self) -> bool:
        return self.tier == "noise"

    @property
    def use_llm(self) -> bool:
        return self.tier in ("medium", "high")


# =============================================================================
# Helpers
# =============================================================================

def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _word_count(text: str) -> int:
    return len(text.split())


def _build_ado_search_text(fields: dict, comments: list[dict]) -> str:
    """Concatenate all readable text from a work item for keyword scanning."""
    parts = [
        fields.get("System.Title", ""),
        _strip_html(fields.get("System.Description", "")),
        _strip_html(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "")),
        _strip_html(fields.get("Microsoft.VSTS.TCM.ReproSteps", "")),
        fields.get("Microsoft.VSTS.Common.ResolvedReason", ""),
        fields.get("System.Tags", ""),
    ]
    for c in comments:
        parts.append(c.get("text", "") if isinstance(c, dict) else "")
    return " ".join(p for p in parts if p)


# =============================================================================
# ADO heuristic scorer
# =============================================================================

def score_ado_item(
    fields: dict,
    comments: list[dict],
) -> ADOQualityResult:
    """
    Score a resolved/closed ADO bug or issue.

    Parameters
    ----------
    fields : dict
        The 'fields' dict from the ADO REST API work item response.
    comments : list[dict]
        The list of comment objects from the ADO comments API.
        Each should have a 'text' key.
    """
    reasons:  list[str] = []
    score = 0.0

    all_text = _build_ado_search_text(fields, comments)

    # ------------------------------------------------------------------
    # Disqualifying keywords — instant noise, no further scoring
    # ------------------------------------------------------------------
    disqualify_hits = check_keywords(all_text, ADO_DISQUALIFY)
    if disqualify_hits:
        return ADOQualityResult(
            score=0.0,
            tier="noise",
            reasons=[f"Disqualified by: {', '.join(disqualify_hits)}"],
            disqualify_hits=disqualify_hits,
        )

    # ------------------------------------------------------------------
    # Description quality  (0 – 0.25)
    # ------------------------------------------------------------------
    description = _strip_html(fields.get("System.Description", ""))
    desc_words  = _word_count(description)

    if desc_words >= 150:
        score += 0.25
        reasons.append(f"Rich description ({desc_words} words)")
    elif desc_words >= 75:
        score += 0.15
        reasons.append(f"Adequate description ({desc_words} words)")
    elif desc_words >= 20:
        score += 0.07
        reasons.append(f"Sparse description ({desc_words} words)")
    else:
        reasons.append("Description missing or too short")

    # ------------------------------------------------------------------
    # Repro steps  (0 – 0.15)
    # ------------------------------------------------------------------
    repro = _strip_html(fields.get("Microsoft.VSTS.TCM.ReproSteps", ""))
    if _word_count(repro) >= 30:
        score += 0.15
        reasons.append("Has repro steps")
    elif _word_count(repro) >= 5:
        score += 0.07
        reasons.append("Partial repro steps")

    # ------------------------------------------------------------------
    # Comments  (0 – 0.25)
    # Comments are the most valuable field for resolved bugs —
    # they contain root cause analysis, workarounds, and the actual fix.
    # ------------------------------------------------------------------
    comment_count = len([c for c in comments if c.get("text", "").strip()])

    if comment_count >= 5:
        score += 0.25
        reasons.append(f"{comment_count} comments (high signal)")
    elif comment_count >= 3:
        score += 0.18
        reasons.append(f"{comment_count} comments")
    elif comment_count >= 1:
        score += 0.10
        reasons.append(f"{comment_count} comment(s)")
    else:
        reasons.append("No comments — resolution context likely missing")

    # ------------------------------------------------------------------
    # Resolution reason  (0 – 0.15)
    # Generic reasons ("Fixed", "Done") score less than specific ones.
    # ------------------------------------------------------------------
    resolved_reason = (fields.get("Microsoft.VSTS.Common.ResolvedReason") or "").strip()
    generic_reasons = {"fixed", "done", "completed", "resolved", "closed"}

    if resolved_reason and resolved_reason.lower() not in generic_reasons:
        score += 0.15
        reasons.append(f"Specific resolution reason: '{resolved_reason}'")
    elif resolved_reason:
        score += 0.05
        reasons.append(f"Generic resolution reason: '{resolved_reason}'")
    else:
        reasons.append("No resolution reason recorded")

    # ------------------------------------------------------------------
    # Tags  (0 – 0.10)
    # ------------------------------------------------------------------
    tags = [t.strip() for t in (fields.get("System.Tags") or "").split(";") if t.strip()]
    if len(tags) >= 2:
        score += 0.10
        reasons.append(f"Tagged: {', '.join(tags)}")
    elif len(tags) == 1:
        score += 0.05
        reasons.append(f"Tagged: {tags[0]}")

    # ------------------------------------------------------------------
    # Acceptance criteria  (0 – 0.10)
    # ------------------------------------------------------------------
    ac = _strip_html(fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", ""))
    if _word_count(ac) >= 20:
        score += 0.10
        reasons.append("Has acceptance criteria")

    # ------------------------------------------------------------------
    # Noise keyword penalty  (-0.10 per hit, floored at 0)
    # ------------------------------------------------------------------
    noise_hits = check_keywords(all_text, ADO_NOISE)
    if noise_hits:
        penalty = min(0.30, 0.10 * len(noise_hits))
        score   = max(0.0, score - penalty)
        reasons.append(f"Noise penalty -{penalty:.2f}: {', '.join(noise_hits)}")

    score = round(min(1.0, score), 4)
    return ADOQualityResult(
        score           = score,
        tier            = _to_tier(score),
        reasons         = reasons,
        noise_hits      = noise_hits,
    )


# =============================================================================
# Teams heuristic scorer
# =============================================================================

def score_teams_thread(
    messages: list[GraphMessage],
    cleaned_map: dict[str, str],
) -> TeamsQualityResult:
    """
    Score a Teams channel thread for ingestion quality.

    Parameters
    ----------
    messages : list[GraphMessage]
        All messages in the thread (root + replies), parsed into GraphMessage.
    cleaned_map : dict[str, str]
        message.id → cleaned plain text (after html2text + length filter).
        Only user messages that passed the cleaning step should be in here.
    """
    reasons: list[str] = []
    score = 0.0

    user_messages    = [m for m in messages if m.id in cleaned_map]
    reply_count      = max(0, len(user_messages) - 1)  # root doesn't count as a reply
    unique_senders   = len({m.sender for m in user_messages})
    all_text         = "\n".join(cleaned_map.values())

    # ------------------------------------------------------------------
    # Hard requirement: must have at least 1 reply
    # (caller should pre-filter but we enforce here too)
    # ------------------------------------------------------------------
    if reply_count < 1:
        return TeamsQualityResult(
            score=0.0,
            tier="noise",
            reasons=["No replies — single message thread"],
            reply_count=0,
        )

    # ------------------------------------------------------------------
    # Disqualifying keywords
    # ------------------------------------------------------------------
    disqualify_hits = check_keywords(all_text, TEAMS_DISQUALIFY)
    if disqualify_hits:
        return TeamsQualityResult(
            score=0.0,
            tier="noise",
            reasons=[f"Disqualified by: {', '.join(disqualify_hits)}"],
            disqualify_hits=disqualify_hits,
            reply_count=reply_count,
        )

    # ------------------------------------------------------------------
    # Reply count  (0 – 0.25)
    # ------------------------------------------------------------------
    if reply_count >= 10:
        score += 0.25
        reasons.append(f"{reply_count} replies")
    elif reply_count >= 5:
        score += 0.18
        reasons.append(f"{reply_count} replies")
    elif reply_count >= 3:
        score += 0.12
        reasons.append(f"{reply_count} replies")
    else:
        score += 0.06
        reasons.append(f"{reply_count} reply")

    # ------------------------------------------------------------------
    # Unique participants  (0 – 0.25)
    # More participants = richer perspective = more valuable thread.
    # ------------------------------------------------------------------
    if unique_senders >= 5:
        score += 0.25
        reasons.append(f"{unique_senders} unique participants")
    elif unique_senders >= 3:
        score += 0.18
        reasons.append(f"{unique_senders} participants")
    elif unique_senders == 2:
        score += 0.10
        reasons.append("2 participants")
    else:
        reasons.append("Single participant — monologue thread")

    # ------------------------------------------------------------------
    # Average message length  (0 – 0.20)
    # Short messages are usually reactions or acknowledgements.
    # ------------------------------------------------------------------
    avg_words = (
        sum(_word_count(t) for t in cleaned_map.values()) / len(cleaned_map)
        if cleaned_map else 0
    )
    if avg_words >= 40:
        score += 0.20
        reasons.append(f"Substantive messages (avg {avg_words:.0f} words)")
    elif avg_words >= 20:
        score += 0.12
        reasons.append(f"Moderate messages (avg {avg_words:.0f} words)")
    elif avg_words >= 10:
        score += 0.06
    else:
        reasons.append("Very short messages — likely social thread")

    # ------------------------------------------------------------------
    # Technical content signal  (0 – 0.20)
    # Detect patterns that suggest engineering discussion.
    # ------------------------------------------------------------------
    tech_patterns = [
        r"\berror\b",
        r"\bexception\b",
        r"\bstack trace\b",
        r"\bapi\b",
        r"\bendpoint\b",
        r"\bconfig\b",
        r"\bdeploy",
        r"\bpipeline\b",
        r"\btimeout\b",
        r"\bnull\b",
        r"\b5[0-9]{2}\b",            # HTTP 5xx
        r"\b4[0-9]{2}\b",            # HTTP 4xx
        r"https?://",
        r"\b[a-f0-9]{6,}\b",         # hex hashes / IDs
        r"```",                       # code blocks survived cleaning
    ]
    tech_hits = sum(
        1 for p in tech_patterns
        if re.search(p, all_text, re.IGNORECASE)
    )
    if tech_hits >= 5:
        score += 0.20
        reasons.append(f"Strong technical content ({tech_hits} signals)")
    elif tech_hits >= 3:
        score += 0.12
        reasons.append(f"Technical content ({tech_hits} signals)")
    elif tech_hits >= 1:
        score += 0.06
        reasons.append("Mild technical content")
    else:
        reasons.append("No technical signals detected")

    # ------------------------------------------------------------------
    # Noise keyword penalty
    # ------------------------------------------------------------------
    noise_hits = check_keywords(all_text, TEAMS_NOISE)
    if noise_hits:
        penalty = min(0.25, 0.08 * len(noise_hits))
        score   = max(0.0, score - penalty)
        reasons.append(f"Noise penalty -{penalty:.2f}: {', '.join(noise_hits)}")

    score = round(min(1.0, score), 4)
    return TeamsQualityResult(
        score               = score,
        tier                = _to_tier(score),
        reasons             = reasons,
        noise_hits          = noise_hits,
        reply_count         = reply_count,
        unique_participants = unique_senders,
    )
