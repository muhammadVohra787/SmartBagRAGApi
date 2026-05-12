"""
app/clients/graph.py

MS Graph API client — Teams channel messages, replies, and image attachments.

All methods are synchronous (httpx sync client). For the mass batch pipeline
they run sequentially; move to AsyncClient later if throughput becomes a concern.

Permissions required on the App Registration (application-type, admin consented):
    ChannelMessage.Read.All
    Team.ReadBasic.All
    Files.Read.All          (to download SharePoint-hosted attachments)
    User.Read.All           (to resolve display names)

Graph API docs:
    https://learn.microsoft.com/en-us/graph/api/channel-list-messages
    https://learn.microsoft.com/en-us/graph/api/chatmessage-list-replies
"""

from __future__ import annotations

import logging
import time
from typing import Iterator

import httpx

from app.clients.auth import graph_headers

log = logging.getLogger(__name__)

GRAPH_BASE   = "https://graph.microsoft.com/v1.0"
PAGE_SIZE    = 50     # Graph default max per page for channel messages
RETRY_LIMIT  = 3
RETRY_DELAY  = 2.0    # seconds, doubles on each retry


# =============================================================================
# Low-level HTTP with retry + 429 handling
# =============================================================================

def _get(url: str, params: dict | None = None) -> dict:
    """
    Authenticated GET against Graph. Handles 429 (rate limit) and transient 5xx.
    Returns parsed JSON body.
    """
    for attempt in range(1, RETRY_LIMIT + 1):
        resp = httpx.get(url, headers=graph_headers(), params=params, timeout=30)

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", RETRY_DELAY * attempt))
            log.warning("Graph 429 — waiting %ds (attempt %d/%d)", retry_after, attempt, RETRY_LIMIT)
            time.sleep(retry_after)
            continue

        if resp.status_code in (500, 502, 503, 504) and attempt < RETRY_LIMIT:
            log.warning("Graph %d — retrying in %ds", resp.status_code, RETRY_DELAY * attempt)
            time.sleep(RETRY_DELAY * attempt)
            continue

        resp.raise_for_status()

    raise RuntimeError(f"Graph GET failed after {RETRY_LIMIT} attempts: {url}")


def _get_bytes(url: str) -> bytes:
    """Download raw bytes (for images / file attachments)."""
    for attempt in range(1, RETRY_LIMIT + 1):
        resp = httpx.get(url, headers=graph_headers(), timeout=60, follow_redirects=True)
        if resp.status_code == 200:
            return resp.content
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", RETRY_DELAY * attempt)))
            continue
        if resp.status_code in (500, 502, 503, 504) and attempt < RETRY_LIMIT:
            time.sleep(RETRY_DELAY * attempt)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"Graph download failed: {url}")


def _paginate(first_url: str, params: dict | None = None) -> Iterator[dict]:
    """
    Yield every item from a paginated Graph endpoint.
    Follows @odata.nextLink automatically.
    """
    url = first_url
    while url:
        body = _get(url, params=params if url == first_url else None)
        for item in body.get("value", []):
            yield item
        url = body.get("@odata.nextLink")   # None when last page


# =============================================================================
# Teams / Channels
# =============================================================================

def list_joined_teams() -> list[dict]:
    """
    Return all teams the app has access to.
    Uses /teams endpoint (requires Team.ReadBasic.All).
    Falls back to /me/joinedTeams if app-level permission is absent.
    """
    try:
        return list(_paginate(f"{GRAPH_BASE}/teams"))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 403:
            log.warning("Cannot list all teams — falling back to joinedTeams")
            return list(_paginate(f"{GRAPH_BASE}/me/joinedTeams"))
        raise


def list_channels(team_id: str) -> list[dict]:
    """Return all channels in a team."""
    return list(_paginate(f"{GRAPH_BASE}/teams/{team_id}/channels"))


# =============================================================================
# Messages
# =============================================================================

def get_channel_messages(team_id: str, channel_id: str) -> list[dict]:
    """
    Fetch all messages in a channel (root messages only, no replies).
    Returns newest-first from Graph; we reverse to chronological.
    """
    url    = f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages"
    msgs   = list(_paginate(url, params={"$top": PAGE_SIZE}))
    log.info("Fetched %d root messages from channel %s", len(msgs), channel_id)
    return msgs   # Graph returns newest-first; callers can reverse if needed


def get_message_replies(team_id: str, channel_id: str, message_id: str) -> list[dict]:
    """
    Fetch all replies to a specific root message.
    Replies are returned oldest-first by Graph.
    """
    url     = f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies"
    replies = list(_paginate(url, params={"$top": PAGE_SIZE}))
    log.debug("Fetched %d replies for message %s", len(replies), message_id)
    return replies


def get_full_thread(team_id: str, channel_id: str, message_id: str) -> list[dict]:
    """
    Fetch the root message + all replies for a thread.
    Returns a flat list: [root, reply1, reply2, ...] in chronological order.
    This is the main method the batch pipeline calls.
    """
    # Fetch root message
    url  = f"{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages/{message_id}"
    root = _get(url)

    # Fetch replies
    replies = get_message_replies(team_id, channel_id, message_id)

    # Sort everything chronologically
    all_msgs = [root] + sorted(replies, key=lambda m: m.get("createdDateTime", ""))
    log.info(
        "Thread %s: %d messages total (%d replies)",
        message_id, len(all_msgs), len(replies),
    )
    return all_msgs


def get_threads_with_replies(
    team_id:    str,
    channel_id: str,
    min_replies: int = 1,
) -> list[list[dict]]:
    """
    Fetch all threads in a channel that have at least min_replies replies.
    Returns a list of threads, where each thread is [root, reply1, ...].

    Used by the mass Teams ingestion pipeline to pull all qualifying threads
    from a channel in one go.
    """
    root_messages = get_channel_messages(team_id, channel_id)
    threads: list[list[dict]] = []

    for msg in root_messages:
        reply_count = msg.get("replies@odata.count") or msg.get("replyCount", 0)

        # Skip if reply count metadata says 0 (avoid unnecessary API calls)
        if isinstance(reply_count, int) and reply_count < min_replies:
            continue

        try:
            thread = get_full_thread(team_id, channel_id, msg["id"])
            # Count actual user replies (not system messages)
            real_replies = sum(
                1 for m in thread[1:]
                if m.get("messageType") == "message"
            )
            if real_replies >= min_replies:
                threads.append(thread)
        except Exception as exc:
            log.warning("Failed to fetch replies for message %s: %s", msg["id"], exc)

    log.info(
        "Channel %s: %d threads with >= %d replies",
        channel_id, len(threads), min_replies,
    )
    return threads


# =============================================================================
# Image / attachment extraction
# =============================================================================

# Image MIME types we handle
IMAGE_MIMES = {
    "image/png", "image/jpeg", "image/jpg",
    "image/gif", "image/bmp", "image/webp",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def extract_image_attachments(message: dict) -> list[dict]:
    """
    Extract image attachments from a single Graph message dict.

    Returns a list of dicts:
        {
            "name":         str,    # filename
            "content_type": str,    # MIME type if known
            "url":          str,    # download URL
        }

    Teams images come from two places:
    1. message["attachments"] — file references uploaded to the chat
    2. Inline <img> tags inside message["body"]["content"] (HTML)
       These have src="https://graph.microsoft.com/..." URLs.
    """
    images: list[dict] = []
    seen_urls: set[str] = set()

    # --- Attachment references -----------------------------------------------
    for att in message.get("attachments", []):
        name    = att.get("name", "")
        url     = att.get("contentUrl") or att.get("content") or ""
        c_type  = att.get("contentType", "")

        # Teams file references have contentType "reference" but we detect by name
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""

        if ext in IMAGE_EXTENSIONS and url and url not in seen_urls:
            images.append({"name": name, "content_type": c_type, "url": url})
            seen_urls.add(url)

    # --- Inline images in HTML body ------------------------------------------
    import re
    html_body = message.get("body", {}).get("content", "")
    if message.get("body", {}).get("contentType") == "html":
        for src in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html_body, re.IGNORECASE):
            if src not in seen_urls:
                # Graph-hosted inline images: identifiable by graph.microsoft.com domain
                ext = "." + src.rsplit(".", 1)[-1].lower().split("?")[0] if "." in src else ""
                if "graph.microsoft.com" in src or ext in IMAGE_EXTENSIONS:
                    images.append({"name": f"inline_{len(images)}.png", "content_type": "image/png", "url": src})
                    seen_urls.add(src)

    return images


def download_image(url: str) -> bytes:
    """
    Download an image from a Graph URL.
    Returns raw bytes. Caller converts to base64 for the Vision API.
    """
    log.debug("Downloading image: %s", url[:80])
    return _get_bytes(url)
