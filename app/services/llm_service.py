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

ANSWER_SYSTEM = (
    "You are a helpful assistant that answers questions based solely on the provided context documents. "
    "Your answers must be grounded in the given context. "
    "If the answer is not in the context, clearly state that you don't have enough information. "
    "Always cite which documents you used by referring to their titles."
)

ANSWER_USER = """\
Context Documents:

{context}

Question: {query}

Please answer the question using only the information from the context documents above. Cite your sources by mentioning the document titles.
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
        model=settings.azure_openai_deployment_name,
        max_tokens=800,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        messages=[
            {"role": "system", "content": TEAMS_SUMMARY_SYSTEM},
            {"role": "user",   "content": TEAMS_SUMMARY_USER.format(thread_text=thread_text)},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM returned empty summary")
    return content.strip()


# ---------------------------------------------------------------------------
# Answer Synthesis (Query Q&A)
# ---------------------------------------------------------------------------

def synthesize_answer(query: str, documents: list[dict]) -> str:
    """
    Generate an answer to the user's query based on retrieved documents.

    Parameters:
        query: The user's question
        documents: List of dicts with keys: title, source_type, content

    Returns:
        Synthesized answer with citations

    Raises:
        Exception on API error — callers should catch and handle
    """
    # Format context from documents
    context_blocks = []
    for i, doc in enumerate(documents, 1):
        title = doc.get("title", "Untitled")
        source_type = doc.get("source_type", "unknown")
        content = doc.get("content", "")

        context_blocks.append(
            f"[Document {i}: {title}]\n"
            f"Source Type: {source_type}\n"
            f"Content: {content}\n"
        )

    context = "\n".join(context_blocks)

    client = get_client()
    response = client.chat.completions.create(
        model=settings.azure_openai_deployment_name,
        max_tokens=1000,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout_seconds,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user",   "content": ANSWER_USER.format(context=context, query=query)},
        ],
    )

    content = response.choices[0].message.content
    if not content:
        raise ValueError("LLM returned empty answer")

    return content.strip()
