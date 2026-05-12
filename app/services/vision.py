"""
app/services/vision.py

GPT-4o Vision service — converts image bytes to descriptive text
that gets appended to work item or Teams thread content before embedding.

Uses the same Azure OpenAI client as the LLM service.
Images are sent as base64-encoded data URIs in the message content.

Max image size: 20 MB (Azure OpenAI limit).
Images larger than this are skipped with a warning.
"""

from __future__ import annotations

import base64
import logging

from app.core.settings import settings
from app.services.llm_service import get_client

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 20 * 1024 * 1024   # 20 MB

# Prompt is the same regardless of source (ADO or Teams)
_VISION_SYSTEM = (
    "You are a technical documentation assistant. "
    "Analyse images attached to software bug reports or engineering discussions. "
    "Be specific and technical. Focus on what is relevant to understanding the issue."
)

_VISION_USER = """\
This image is attached to a {source_type}. Extract all technically relevant information:

- Any error messages, exception text, or status codes visible
- Any UI state that appears broken, unexpected, or relevant to the issue
- Any stack traces, log output, or console text
- Any configuration values, endpoints, or system identifiers visible
- Any diagrams, flow charts, or architecture elements

Be concise. Ignore decorative or irrelevant visual elements.
If the image contains no useful technical information, respond with: "No technical content."
"""


def describe_image(
    image_bytes:  bytes,
    content_type: str,
    source_type:  str = "software bug report",
    filename:     str = "",
) -> str | None:
    """
    Send an image to GPT-4o Vision and return a plain-text description.

    Parameters
    ----------
    image_bytes :  bytes  raw image data
    content_type : str    MIME type, e.g. "image/png"
    source_type :  str    context label for the prompt ("ADO work item" / "Teams thread")
    filename :     str    optional filename for logging

    Returns
    -------
    str   description text, or None if the image was skipped / failed
    """
    if not image_bytes:
        log.warning("Empty image bytes for %s — skipping", filename)
        return None

    if len(image_bytes) > MAX_IMAGE_BYTES:
        log.warning(
            "Image %s is %.1f MB — exceeds 20 MB limit, skipping",
            filename, len(image_bytes) / 1024 / 1024,
        )
        return None

    # Normalise MIME type
    if content_type not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        content_type = "image/png"   # safe default for bmp / unknown

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    data_uri = f"data:{content_type};base64,{b64}"

    try:
        client   = get_client()
        response = client.chat.completions.create(
            model=settings.azure_openai_deployment_name,
            max_tokens=400,
            temperature=0.1,
            messages=[
                {"role": "system", "content": _VISION_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": _VISION_USER.format(source_type=source_type),
                        },
                        {
                            "type":      "image_url",
                            "image_url": {"url": data_uri, "detail": "high"},
                        },
                    ],
                },
            ],
        )

        description = (response.choices[0].message.content or "").strip()

        if not description or description == "No technical content.":
            log.debug("Image %s: no technical content", filename)
            return None

        log.debug("Image %s: described (%d chars)", filename, len(description))
        return description

    except Exception as exc:
        log.warning("Vision API failed for %s: %s", filename, exc)
        return None


def describe_images(
    images:      list[dict],
    download_fn: callable,
    source_type: str = "software bug report",
) -> str:
    """
    Describe multiple images and return a combined text block.

    Parameters
    ----------
    images :      list[dict]  from graph.extract_image_attachments()
                              or ado.extract_image_attachments()
                              Each dict has: name, url, content_type
    download_fn : callable    function that takes a URL and returns bytes
                              (graph.download_image or ado.download_attachment)
    source_type : str         context label for the prompt

    Returns
    -------
    str   "Attached Images:\n  [img1] description\n  [img2] ..."
          or "" if no descriptions were generated
    """
    descriptions: list[str] = []

    for img in images:
        name = img.get("name", "image")
        url  = img.get("url", "")

        if not url:
            continue

        try:
            raw = download_fn(url)
        except Exception as exc:
            log.warning("Failed to download image %s: %s", name, exc)
            continue

        desc = describe_image(
            image_bytes  = raw,
            content_type = img.get("content_type", "image/png"),
            source_type  = source_type,
            filename     = name,
        )

        if desc:
            descriptions.append(f"  [{name}] {desc}")

    if not descriptions:
        return ""

    return "Attached Images:\n" + "\n".join(descriptions)
