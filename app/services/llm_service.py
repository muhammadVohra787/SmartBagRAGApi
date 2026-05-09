"""
services/llm.py

Azure OpenAI client for:
  - Teams thread summarisation (ingestion)
  - Q&A answer generation (query)

Uses the standard openai SDK pointed at the Azure endpoint via environment config.
The deployment name (e.g. "gpt-4o") is set in .env — the SDK maps it to the
Azure deployment automatically.
"""

import logging

from openai import AzureOpenAI

from app.core.settings import settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------

_client: AzureOpenAI | None = None


def get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            api_key=settings.azure_openai_api_key,
            azure_endpoint=settings.azure_openai_endpoint,
            api_version=settings.azure_openai_api_version,
        )
    return _client


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

TEAMS_SUMMARY_SYSTEM = (
    "You are a technical knowledge base assistant for a software engineering team. "
    "Your job is to extract structured, factual information from internal MS Teams threads "
    "so it can be searched and retrieved later. Be concise and specific."
)

TEAMS_SUMMARY_USER = """\
Summarise the following Teams thread. Return exactly these five sections:

1. Topic — one sentence describing the subject of the thread.
2. Key Decisions — bullet list of any decisions made or conclusions reached. If none, write "None".
3. Action Items — bullet list of tasks assigned or follow-ups mentioned. If none, write "None".
4. Technical Details — any config values, system names, API names, version numbers, or code-level specifics mentioned.
5. Participants — comma-separated list of people who contributed meaningfully.

Thread:
{thread_text}
"""

# ---------------------------------------------------------------------------
# Summarisation (Teams ingestion)
# ---------------------------------------------------------------------------

def summarise_thread(thread_text: str) -> str:
    """
    Send the flattened thread text to Azure OpenAI and return the summary string.
    Raises on API error — callers should catch and fall back to raw text.
    """
    client = get_client()
    response = client.chat.completions.create(
        model=settings.azure_openai_deployment,
        max_tokens=800,
        temperature=0.2,          # low temperature — we want factual extraction, not creativity
        messages=[
            {"role": "system", "content": TEAMS_SUMMARY_SYSTEM},
            {"role": "user",   "content": TEAMS_SUMMARY_USER.format(thread_text=thread_text)},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM returned empty summary")
    return content.strip()
