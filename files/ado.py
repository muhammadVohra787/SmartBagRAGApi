"""
app/clients/ado.py

Azure DevOps REST API client — work items, comments, and image attachments.

Auth: Azure AD bearer token via RAG_API_* credentials (same app registration
as Graph). Falls back to PAT (ado_pat in settings) if Azure AD creds are absent.

ADO API docs:
    https://learn.microsoft.com/en-us/rest/api/azure/devops/wit

Key endpoints used:
    WIQL query      POST /{org}/{project}/_apis/wit/wiql
    Batch fetch     GET  /{org}/_apis/wit/workitems?ids=...&$expand=all
    Comments        GET  /{org}/{project}/_apis/wit/workitems/{id}/comments
    Attachments     GET  /{url-from-relation}
"""

from __future__ import annotations

import base64
import logging
import time
from itertools import islice
from typing import Iterator

import httpx

from app.clients.auth import ado_headers
from app.config import settings

log = logging.getLogger(__name__)

ADO_API_VERSION = "7.1"
BATCH_SIZE      = 200     # ADO max work items per batch request
RETRY_LIMIT     = 3
RETRY_DELAY     = 2.0

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


# =============================================================================
# Auth headers — Azure AD preferred, PAT fallback
# =============================================================================

def _headers() -> dict[str, str]:
    """
    Use Azure AD token if RAG_API_* creds are set, otherwise fall back to PAT.
    PAT is base64-encoded as 'Basic :<token>'.
    """
    if settings.rag_api_client_id and settings.rag_api_client_secret:
        return ado_headers()

    if settings.ado_pat:
        encoded = base64.b64encode(f":{settings.ado_pat}".encode()).decode()
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type":  "application/json",
        }

    raise EnvironmentError(
        "No ADO credentials found. "
        "Set RAG_API_CLIENT_ID + RAG_API_CLIENT_SECRET (Azure AD) "
        "or ADO_PAT in your environment."
    )


# =============================================================================
# Low-level HTTP
# =============================================================================

def _get(url: str, params: dict | None = None) -> dict:
    for attempt in range(1, RETRY_LIMIT + 1):
        resp = httpx.get(url, headers=_headers(), params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", RETRY_DELAY * attempt))
            log.warning("ADO 429 — waiting %ds", wait)
            time.sleep(wait)
            continue
        if resp.status_code in (500, 502, 503, 504) and attempt < RETRY_LIMIT:
            time.sleep(RETRY_DELAY * attempt)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"ADO GET failed after {RETRY_LIMIT} attempts: {url}")


def _post(url: str, body: dict, params: dict | None = None) -> dict:
    for attempt in range(1, RETRY_LIMIT + 1):
        resp = httpx.post(url, headers=_headers(), json=body, params=params, timeout=30)
        if resp.status_code in (200, 201):
            return resp.json()
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", RETRY_DELAY * attempt)))
            continue
        if resp.status_code in (500, 502, 503, 504) and attempt < RETRY_LIMIT:
            time.sleep(RETRY_DELAY * attempt)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"ADO POST failed: {url}")


def _get_bytes(url: str) -> bytes:
    for attempt in range(1, RETRY_LIMIT + 1):
        resp = httpx.get(url, headers=_headers(), timeout=60, follow_redirects=True)
        if resp.status_code == 200:
            return resp.content
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", RETRY_DELAY * attempt)))
            continue
        if resp.status_code in (500, 502, 503, 504) and attempt < RETRY_LIMIT:
            time.sleep(RETRY_DELAY * attempt)
            continue
        resp.raise_for_status()
    raise RuntimeError(f"ADO download failed: {url}")


def _batched(iterable, n: int) -> Iterator[list]:
    """Yield successive n-sized chunks from iterable."""
    it = iter(iterable)
    while chunk := list(islice(it, n)):
        yield chunk


# =============================================================================
# WIQL — query for work item IDs
# =============================================================================

def query_work_item_ids(
    org:          str,
    project:      str,
    states:       list[str] | None = None,
    types:        list[str] | None = None,
    top:          int | None = None,
) -> list[int]:
    """
    Run a WIQL query and return matching work item IDs.

    Parameters
    ----------
    org :     str   e.g. "https://dev.azure.com/your-company"
    project : str   project name
    states :  list  default ["Resolved", "Closed"]
    types :   list  default ["Bug", "Issue"]
    top :     int   optional limit (for testing — omit for full pull)

    Returns
    -------
    list[int]  work item IDs in descending order of last-changed date
    """
    states = states or ["Resolved", "Closed"]
    types  = types  or ["Bug", "Issue"]

    state_list = ", ".join(f"'{s}'" for s in states)
    type_list  = ", ".join(f"'{t}'" for t in types)

    wiql = (
        f"SELECT [System.Id] FROM WorkItems "
        f"WHERE [System.WorkItemType] IN ({type_list}) "
        f"AND [System.State] IN ({state_list}) "
        f"AND [System.TeamProject] = '{project}' "
        f"ORDER BY [System.ChangedDate] DESC"
    )

    url    = f"{org}/{project}/_apis/wit/wiql"
    params = {"api-version": ADO_API_VERSION}
    if top:
        params["$top"] = top

    body    = {"query": wiql}
    result  = _post(url, body, params=params)

    ids = [ref["id"] for ref in result.get("workItems", [])]
    log.info("WIQL returned %d work item IDs (org=%s project=%s)", len(ids), org, project)
    return ids


# =============================================================================
# Work item fetch
# =============================================================================

def get_work_items_batch(org: str, ids: list[int]) -> list[dict]:
    """
    Batch-fetch work items with full field expansion.
    ADO allows max 200 IDs per request — this handles chunking automatically.

    Returns a flat list of raw work item dicts.
    """
    if not ids:
        return []

    all_items: list[dict] = []
    url = f"{org}/_apis/wit/workitems"

    for chunk in _batched(ids, BATCH_SIZE):
        params = {
            "ids":          ",".join(str(i) for i in chunk),
            "$expand":      "all",           # includes relations (attachments)
            "api-version":  ADO_API_VERSION,
        }
        body = _get(url, params=params)
        all_items.extend(body.get("value", []))
        log.debug("Fetched batch of %d work items", len(chunk))

    log.info("Fetched %d work items total", len(all_items))
    return all_items


def get_work_item_comments(org: str, project: str, work_item_id: int) -> list[dict]:
    """
    Fetch all comments for a work item.
    Returns raw comment dicts from the ADO comments API.
    """
    url    = f"{org}/{project}/_apis/wit/workitems/{work_item_id}/comments"
    params = {"api-version": f"{ADO_API_VERSION}-preview.3", "$expand": "all"}

    try:
        body = _get(url, params=params)
        comments = body.get("comments", [])
        log.debug("Work item %d: %d comments", work_item_id, len(comments))
        return comments
    except httpx.HTTPStatusError as exc:
        log.warning("Could not fetch comments for #%d: %s", work_item_id, exc)
        return []


def get_full_work_item(org: str, project: str, work_item_id: int) -> dict:
    """
    Fetch a single work item with full expansion plus its comments.
    Returns the raw work item dict with a 'comments' key injected.

    This is what gets passed to ADOWorkItemRaw.model_validate().
    """
    url    = f"{org}/{project}/_apis/wit/workitems/{work_item_id}"
    params = {"$expand": "all", "api-version": ADO_API_VERSION}
    item   = _get(url, params=params)

    comments = get_work_item_comments(org, project, work_item_id)
    item["comments"] = {"value": comments}

    return item


def pull_all_bugs(
    org:     str,
    project: str,
    states:  list[str] | None = None,
    types:   list[str] | None = None,
    top:     int | None = None,
) -> list[dict]:
    """
    Full pipeline: WIQL query → batch fetch → inject comments.

    This is the single method the mass ingest batch calls to get all
    1,300+ bug tickets with their fields and comments in one go.

    Parameters
    ----------
    org :     str   e.g. "https://dev.azure.com/your-company"
    project : str   ADO project name
    states :  list  default ["Resolved", "Closed"]
    types :   list  default ["Bug", "Issue"]
    top :     int   optional limit for testing (e.g. top=10)

    Returns
    -------
    list[dict]  raw work item dicts, each with comments["value"] injected
    """
    ids   = query_work_item_ids(org, project, states=states, types=types, top=top)
    items = get_work_items_batch(org, ids)

    log.info("Injecting comments for %d items...", len(items))
    for item in items:
        item_id  = item["id"]
        comments = get_work_item_comments(org, project, item_id)
        item["comments"] = {"value": comments}

    log.info("pull_all_bugs complete: %d items with comments", len(items))
    return items


# =============================================================================
# Image / attachment extraction
# =============================================================================

def extract_image_attachments(work_item: dict) -> list[dict]:
    """
    Extract image attachment metadata from a work item's relations list.

    ADO stores attachments as relations with rel='AttachedFile'.
    Returns a list of dicts:
        {
            "name":         str,    # filename from attributes.name
            "url":          str,    # download URL
            "size":         int,    # bytes (0 if unknown)
            "content_type": str,    # guessed from extension
        }
    """
    images: list[dict] = []

    for relation in work_item.get("relations", []):
        if relation.get("rel") != "AttachedFile":
            continue

        attrs = relation.get("attributes", {})
        name  = attrs.get("name", "")
        url   = relation.get("url", "")
        size  = attrs.get("resourceSize", 0)

        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in IMAGE_EXTENSIONS:
            continue

        # Map extension to MIME type
        mime_map = {
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif":  "image/gif",
            ".bmp":  "image/bmp",
            ".webp": "image/webp",
        }

        images.append({
            "name":         name,
            "url":          url,
            "size":         size,
            "content_type": mime_map.get(ext, "image/png"),
        })

    if images:
        log.debug("Work item %s: found %d image attachments", work_item.get("id"), len(images))

    return images


def download_attachment(url: str) -> bytes:
    """
    Download a work item attachment by its ADO URL.
    Returns raw bytes. Caller converts to base64 for Vision API.
    """
    log.debug("Downloading ADO attachment: %s", url[:80])
    return _get_bytes(url)
