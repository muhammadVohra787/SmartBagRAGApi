"""
config.py — Application settings

Reads the correct .env file based on APP_ENV environment variable.

    APP_ENV=local   → loads .env.local
    APP_ENV=dev     → loads .env.dev
    APP_ENV=prod    → loads .env.prod  (no example provided — secrets come from Key Vault / Secrets Manager)

Usage anywhere in the app:
    from config import settings
    print(settings.qdrant_host)
"""

import os
from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_file() -> str:
    """Pick the right .env file based on APP_ENV. Falls back to .env.local."""
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
        extra="ignore",           # silently ignore unknown keys in the .env file
    )

    # -------------------------------------------------------------------------
    # Runtime environment
    # -------------------------------------------------------------------------
    app_env: str = "local"
    debug: bool = False
    log_level: str = "info"

    @property
    def is_local(self) -> bool:
        return self.app_env == "local"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    # -------------------------------------------------------------------------
    # Qdrant
    # -------------------------------------------------------------------------
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_api_key: str = ""           # empty string = no auth (local Docker)
    qdrant_collection: str = "knowledge_base"

    # -------------------------------------------------------------------------
    # Azure OpenAI
    # -------------------------------------------------------------------------
    azure_openai_api_key: str
    azure_openai_endpoint: str         # https://your-resource.openai.azure.com
    azure_openai_api_version: str = "2024-02-15-preview"
    azure_openai_deployment: str = "gpt-4o"

    # -------------------------------------------------------------------------
    # Embeddings
    # -------------------------------------------------------------------------
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    embedding_device: str = "cpu"      # "cpu" or "cuda"

    # -------------------------------------------------------------------------
    # Azure DevOps
    # -------------------------------------------------------------------------
    ado_pat: str = ""
    ado_org: str = ""                  # https://dev.azure.com/your-org
    ado_webhook_secret: str = ""

    # -------------------------------------------------------------------------
    # Backend API key (ADO extension → backend auth)
    # -------------------------------------------------------------------------
    api_key: str

    # -------------------------------------------------------------------------
    # MS Bot Framework
    # -------------------------------------------------------------------------
    microsoft_app_id: str = ""
    microsoft_app_password: str = ""
    tenant_id: str = ""

    # -------------------------------------------------------------------------
    # MS Graph  (post-MVP Teams ingestion)
    # -------------------------------------------------------------------------
    graph_client_id: str = ""
    graph_client_secret: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Cached settings instance. Import this anywhere:
        from config import settings
    The lru_cache ensures .env is only parsed once per process.
    """
    return Settings()


# Module-level singleton — most of the app imports this directly.
settings = get_settings()
