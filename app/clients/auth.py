"""
app/clients/auth.py

Azure AD token acquisition for MS Graph and Azure DevOps.

Both use the same app registration (RAG_API_CLIENT_ID / SECRET / TENANT_ID)
but different OAuth scopes:
    Graph  → https://graph.microsoft.com/.default
    ADO    → 499b84ac-1321-427f-aa17-267ca6975798/.default
               (this is Microsoft's fixed resource ID for Azure DevOps)

Tokens are cached in-process and refreshed automatically when they expire.
No external library (msal) required — just a POST to the token endpoint.
"""

from __future__ import annotations

import logging
import time

import httpx

from app.core.settings import settings

log = logging.getLogger(__name__)

# Azure AD OAuth2 token endpoint
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

# Fixed resource IDs
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"
_ADO_SCOPE   = "499b84ac-1321-427f-aa17-267ca6975798/.default"

# In-process cache: scope → {"access_token": str, "expires_at": float}
_token_cache: dict[str, dict] = {}


def _fetch_token(scope: str) -> str:
    """
    Request a new access token from Azure AD using client credentials flow.
    Caches the token and returns it before expiry without a new network call.
    """
    cached = _token_cache.get(scope)
    if cached and time.time() < cached["expires_at"] - 60:   # 60s buffer
        return cached["access_token"]

    if not all([settings.rag_api_tenant_id, settings.rag_api_client_id, settings.rag_api_client_secret]):
        raise EnvironmentError(
            "Missing Azure AD credentials. "
            "Set RAG_API_TENANT_ID, RAG_API_CLIENT_ID, RAG_API_CLIENT_SECRET "
            "in your environment or .env file."
        )

    url = _TOKEN_URL.format(tenant_id=settings.rag_api_tenant_id)
    data = {
        "grant_type":    "client_credentials",
        "client_id":     settings.rag_api_client_id,
        "client_secret": settings.rag_api_client_secret,
        "scope":         scope,
    }

    with httpx.Client(timeout=15) as client:
        resp = client.post(url, data=data)

    if resp.status_code != 200:
        raise RuntimeError(
            f"Token request failed ({resp.status_code}): {resp.text[:300]}"
        )

    body = resp.json()

    if "error" in body:
        raise RuntimeError(
            f"Azure AD error: {body['error']} — {body.get('error_description', '')}"
        )

    token      = body["access_token"]
    expires_in = int(body.get("expires_in", 3600))

    _token_cache[scope] = {
        "access_token": token,
        "expires_at":   time.time() + expires_in,
    }

    log.debug("New token acquired for scope=%s expires_in=%ds", scope.split("/")[0], expires_in)
    return token


def get_graph_token() -> str:
    """Bearer token for Microsoft Graph API calls."""
    return _fetch_token(_GRAPH_SCOPE)


def get_ado_token() -> str:
    """Bearer token for Azure DevOps REST API calls."""
    return _fetch_token(_ADO_SCOPE)


def graph_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_graph_token()}",
        "Content-Type":  "application/json",
    }


def ado_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_ado_token()}",
        "Content-Type":  "application/json",
    }
