"""
app/config.py

Loads the correct .env file based on APP_ENV.

    APP_ENV=local  -> .env.local
    APP_ENV=dev    -> .env.dev
    APP_ENV=prod   -> .env.prod

RAG_API_* variables
-------------------
These are the Azure AD credentials used by the data-pull clients
(MS Graph for Teams, ADO REST API for work items).

    RAG_API_TENANT_ID       Azure AD tenant ID
    RAG_API_CLIENT_ID       App registration client / application ID
    RAG_API_CLIENT_SECRET   App registration client secret
    RAG_API_ADO_ORG         https://dev.azure.com/your-company
    RAG_API_ADO_PROJECT     ADO project name
    RAG_API_TEAMS_TEAM_ID   (optional) target a specific team
    RAG_API_TEAMS_CHANNEL_ID(optional) target a specific channel

Usage anywhere in the app:
    from app.config import settings
"""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file() -> str:
    env = os.getenv("APP_ENV", "local").lower()
    mapping = {
        "local": ".env.local",
        "dev":   ".env.dev",
        "prod":  ".env.prod",
    }
    path = mapping.get(env, ".env.local")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Expected env file '{path}' for APP_ENV='{env}'. "
            f"Copy the matching .example file and fill in your values."
        )
    return path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Runtime
    app_env:   str  = "local"
    debug:     bool = False
    log_level: str  = "info"

    @property
    def is_local(self) -> bool: return self.app_env == "local"
    @property
    def is_dev(self)   -> bool: return self.app_env == "dev"
    @property
    def is_prod(self)  -> bool: return self.app_env == "prod"

    # Qdrant
    qdrant_host:       str = "localhost"
    qdrant_port:       int = 6333
    qdrant_api_key:    str = ""
    qdrant_collection: str = "knowledge_base"

    # Azure OpenAI
    azure_openai_api_key:     str = ""
    azure_openai_endpoint:    str = ""
    azure_openai_api_version: str = "2024-02-15-preview"
    azure_openai_deployment:  str = "gpt-4o"

    # Embeddings
    embedding_model:  str = "BAAI/bge-base-en-v1.5"
    embedding_device: str = "cpu"

    # Backend API key (ADO VSIX extension -> backend auth)
    api_key: str = ""

    # ADO - single-item / manual ingest (PAT based)
    ado_pat:            str = ""
    ado_org:            str = ""
    ado_webhook_secret: str = ""

    # MS Bot Framework
    microsoft_app_id:       str = ""
    microsoft_app_password: str = ""

    # -------------------------------------------------------------------------
    # RAG_API_* - Azure AD identity for mass data pull clients
    # Set these in system environment variables or Azure Key Vault.
    # Locally: add to .env.local under the same names.
    # -------------------------------------------------------------------------

    # Azure AD app registration - used for both Graph API (Teams) and ADO OAuth
    rag_api_tenant_id:     str = ""   # Directory (tenant) ID from Azure AD
    rag_api_client_id:     str = ""   # Application (client) ID
    rag_api_client_secret: str = ""   # Client secret value

    # ADO organisation for mass ingestion
    rag_api_ado_org:     str = ""     # https://dev.azure.com/your-company
    rag_api_ado_project: str = ""     # project name

    # Teams scope - leave empty to enumerate all joined teams
    rag_api_teams_team_id:    str = ""
    rag_api_teams_channel_id: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
