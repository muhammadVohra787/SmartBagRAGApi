"""
app/ingestion/mass/prompts.py

LLM prompts for mass ingestion summarisation.

Both prompts return JSON so the output can be parsed deterministically.
Use response_format={"type": "json_object"} on the API call.

JSON keys are fixed — parsers downstream depend on these exact names.
If the LLM omits a key, the parser falls back to an empty string / list.

quality_score (1–5)
    5 = clear root cause, documented fix, high retrieval value
    4 = solid information, minor gaps
    3 = adequate, some gaps
    2 = sparse, limited diagnostic value
    1 = minimal useful information
"""

# =============================================================================
# ADO Resolved Bug / Issue
# =============================================================================

ADO_BUG_SYSTEM = """\
You are a technical knowledge base assistant for a software engineering team.
Your job is to extract structured, factual information from resolved bug tickets
so they can be searched and retrieved later.
Be concise, specific, and technical. Do not add information not present in the ticket.
Return ONLY valid JSON — no markdown, no preamble.\
"""

ADO_BUG_USER = """\
Summarise the following resolved bug ticket as a JSON object with exactly these keys:

{{
  "bug_summary": "One to two sentences — what broke, in what context, how it manifested.",
  "root_cause": "The actual technical cause identified. Pull from comments and resolution. Empty string if not documented.",
  "resolution": "The fix applied or workaround that closed the ticket. Be specific — code change, config update, dependency bump, etc. Empty string if not documented.",
  "affected_components": ["List of systems, services, APIs, features, or teams involved."],
  "reproduction_pattern": "How to reproduce the bug if documented. Empty string if not documented.",
  "quality_score": <integer 1–5>,
  "quality_reason": "One sentence explaining the quality score."
}}

Bug Ticket:
{serialised}
"""

# =============================================================================
# Teams Thread
# =============================================================================

TEAMS_THREAD_SYSTEM = """\
You are a technical knowledge base assistant for a software engineering team.
Your job is to extract structured, factual information from internal MS Teams threads
so they can be searched and retrieved later.
Be concise and specific. Only include information explicitly present in the thread.
Return ONLY valid JSON — no markdown, no preamble.\
"""

TEAMS_THREAD_USER = """\
Summarise the following Teams thread as a JSON object with exactly these keys:

{{
  "topic": "One sentence describing the subject of the thread.",
  "key_decisions": ["Bullet list of decisions made or conclusions reached. Empty list if none."],
  "action_items": ["Bullet list of tasks assigned or follow-ups mentioned. Empty list if none."],
  "technical_details": "Config values, system names, API names, version numbers, error messages, or code-level specifics mentioned. Empty string if none.",
  "participants": ["Comma-separated list of people who contributed meaningfully."],
  "quality_score": <integer 1–5>,
  "quality_reason": "One sentence explaining the quality score."
}}

Thread:
{thread_text}
"""

# =============================================================================
# Parser
# Returns a safe dict even if the LLM output is malformed.
# =============================================================================

import json
import logging

log = logging.getLogger(__name__)

_ADO_DEFAULTS = {
    "bug_summary":          "",
    "root_cause":           "",
    "resolution":           "",
    "affected_components":  [],
    "reproduction_pattern": "",
    "quality_score":        3,
    "quality_reason":       "",
}

_TEAMS_DEFAULTS = {
    "topic":            "",
    "key_decisions":    [],
    "action_items":     [],
    "technical_details":"",
    "participants":     [],
    "quality_score":    3,
    "quality_reason":   "",
}


def parse_ado_response(raw: str) -> dict:
    """
    Parse the LLM JSON response for an ADO bug summary.
    Returns defaults for any missing or unparseable keys.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("ADO summary JSON parse failed — using defaults. Raw: %.200s", raw)
        return dict(_ADO_DEFAULTS)

    result = dict(_ADO_DEFAULTS)
    result.update({k: data[k] for k in _ADO_DEFAULTS if k in data})
    return result


def parse_teams_response(raw: str) -> dict:
    """
    Parse the LLM JSON response for a Teams thread summary.
    Returns defaults for any missing or unparseable keys.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Teams summary JSON parse failed — using defaults. Raw: %.200s", raw)
        return dict(_TEAMS_DEFAULTS)

    result = dict(_TEAMS_DEFAULTS)
    result.update({k: data[k] for k in _TEAMS_DEFAULTS if k in data})
    return result


def format_ado_summary(parsed: dict) -> str:
    """
    Convert a parsed ADO summary dict into a clean plain-text block
    for embedding. More structured than raw serialisation.
    """
    components = ", ".join(parsed["affected_components"]) or "Not specified"
    return (
        f"Bug Summary: {parsed['bug_summary']}\n\n"
        f"Root Cause: {parsed['root_cause'] or 'Not documented'}\n\n"
        f"Resolution: {parsed['resolution'] or 'Not documented'}\n\n"
        f"Affected Components: {components}\n\n"
        f"Reproduction Pattern: {parsed['reproduction_pattern'] or 'Not documented'}"
    ).strip()


def format_teams_summary(parsed: dict) -> str:
    """
    Convert a parsed Teams summary dict into a clean plain-text block for embedding.
    """
    decisions   = "\n".join(f"  • {d}" for d in parsed["key_decisions"])   or "  None"
    actions     = "\n".join(f"  • {a}" for a in parsed["action_items"])     or "  None"
    participants= ", ".join(parsed["participants"])                           or "Unknown"

    return (
        f"Topic: {parsed['topic']}\n\n"
        f"Key Decisions:\n{decisions}\n\n"
        f"Action Items:\n{actions}\n\n"
        f"Technical Details: {parsed['technical_details'] or 'None'}\n\n"
        f"Participants: {participants}"
    ).strip()
