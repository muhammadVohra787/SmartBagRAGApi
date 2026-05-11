"""
app/ingestion/mass/keywords.py

Keyword lists for the heuristic quality gates.

Two categories per source:
    DISQUALIFY  — instant noise tier. Item is skipped regardless of other scores.
    NOISE       — reduces score but does not disqualify. Item may still be ingested.

Matching is case-insensitive, whole-phrase (substring match).

Add to these lists freely — they are the primary tuning mechanism for the gates.
"""

from __future__ import annotations

import re


# =============================================================================
# ADO Bug / Issue keywords
# Context: Resolved and Closed bugs only. We are looking for items with real
# diagnostic value — root cause, reproduction, resolution.
# =============================================================================

# Instant skip — these items carry no knowledge value for the knowledge base.
ADO_DISQUALIFY: list[str] = [
    # Reproduction failures
    "unable to reproduce",
    "cannot reproduce",
    "can't reproduce",
    "could not reproduce",
    "not reproducible",
    "no repro",

    # Intentional / design decisions
    "by design",
    "as designed",
    "working as intended",
    "working as expected",
    "not a bug",
    "not a defect",
    "wai",               # acronym for "working as intended"

    # Duplicates / redirects
    "duplicate of",
    "duplicate ticket",
    "dupe of",
    "marked as duplicate",
    "see ticket",
    "see issue",
    "refer to",

    # Placeholder / test tickets
    "test ticket",
    "test item",
    "test bug",
    "placeholder",
    "ignore this",
    "delete this",
    "do not use",
    "dummy ticket",

    # Stale / abandoned
    "no longer relevant",
    "obsolete",
    "already fixed in",
    "auto-closed",
    "automatically closed",
]

# Score reduction — item may still be valuable but with lower confidence.
ADO_NOISE: list[str] = [
    # Vague resolution
    "intermittent",
    "flaky",
    "cannot consistently reproduce",
    "happens sometimes",
    "randomly",

    # Environment-specific (hard to generalise)
    "customer specific",
    "client specific",
    "environment specific",
    "only on prod",
    "only in staging",
    "one off",

    # Incomplete information
    "no steps to reproduce",
    "no repro steps",
    "tbd",
    "to be determined",
    "to be investigated",
    "under investigation",
    "need more info",
    "waiting for info",
    "awaiting response",

    # External dependency (low internal value)
    "third party",
    "vendor issue",
    "microsoft bug",
    "azure issue",
    "waiting on vendor",

    # Vague fixes
    "works now",
    "fixed itself",
    "resolved on its own",
    "deployed and seems fine",
]


# =============================================================================
# Teams Thread keywords
# Context: Threads with >1 reply in engineering channels.
# =============================================================================

# Instant skip — social noise, admin, bot output, misdirected messages.
TEAMS_DISQUALIFY: list[str] = [
    # Social / acknowledgement only threads
    "wrong channel",
    "please move to",
    "moved to",

    # Test / bot / automated
    "testing 123",
    "test message",
    "automated notification",
    "pipeline notification",
    "build notification",
    "this is a test",

    # Pure admin
    "meeting invite",
    "calendar invite",
    "out of office",
    "ooo response",

    # Zero-content patterns
    "see above",
    "as per my last email",
    "as discussed offline",
    "let's take this offline",    # conversation left Teams — no value here
    "taking this offline",
]

# Score reduction — thread has some signal but quality is limited.
TEAMS_NOISE: list[str] = [
    # Low-signal social patterns
    "fyi only",
    "just fyi",
    "heads up",
    "reminder",
    "gentle reminder",
    "friendly reminder",

    # References elsewhere (value lives outside this thread)
    "see the doc",
    "check confluence",
    "check notion",
    "check the wiki",
    "linked above",
    "see the jira",
    "see the ticket",

    # Inconclusive discussions
    "let's discuss",
    "to be discussed",
    "circle back",
    "follow up needed",
    "no decision yet",
    "still figuring out",

    # Vague agreements
    "sounds good",
    "lgtm",
    "makes sense",
    "agreed",
    "+1",
    "thumbs up",
]


# =============================================================================
# Keyword checker
# =============================================================================

def check_keywords(text: str, keyword_list: list[str]) -> list[str]:
    """
    Return all keywords from keyword_list that appear in text.
    Case-insensitive substring match.
    Empty list means no matches.
    """
    text_lower = text.lower()
    return [kw for kw in keyword_list if kw.lower() in text_lower]


def contains_disqualifier(text: str, keyword_list: list[str]) -> bool:
    """True if any disqualifying keyword appears in text."""
    return bool(check_keywords(text, keyword_list))
