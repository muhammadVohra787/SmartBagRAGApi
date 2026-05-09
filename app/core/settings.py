from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        extra="ignore",
    )

    # ============================================
    # App
    # ============================================

    run_mode: str = Field(
        default="local",
        alias="RUN_MODE",
    )

    app_name: str = Field(
        default="SmartBagRAGApi",
        alias="APP_NAME",
    )

    app_env: str = Field(
        default="local",
        alias="APP_ENV",
    )

    host: str = Field(
        default="0.0.0.0",
        alias="HOST",
    )

    port: int = Field(
        default=8000,
        alias="PORT",
    )

    log_level: str = Field(
        default="INFO",
        alias="LOG_LEVEL",
    )

    # ============================================
    # Qdrant
    # ============================================

    qdrant_url: str = Field(
        default="http://localhost:6333",
        alias="QDRANT_URL",
    )

    qdrant_api_key: str | None = Field(
        default=None,
        alias="QDRANT_API_KEY",
    )

    qdrant_collection_name: str = Field(
        default="knowledge_base",
        alias="QDRANT_COLLECTION_NAME",
    )

    qdrant_distance_metric: str = Field(
        default="Cosine",
        alias="QDRANT_DISTANCE_METRIC",
    )

    # ============================================
    # Embedding
    # ============================================

    embedding_model: str = Field(
        default="BAAI/bge-base-en-v1.5",
        alias="EMBEDDING_MODEL",
    )

    embedding_version: int = Field(
        default=1,
        alias="EMBEDDING_VERSION",
    )

    embedding_dimension: int = Field(
        default=768,
        alias="EMBEDDING_DIMENSION",
    )

    embedding_device: str = Field(
        default="cuda",
        alias="EMBEDDING_DEVICE",
    )

    # ============================================
    # Retrieval
    # ============================================

    max_search_results: int = Field(
        default=20,
        alias="MAX_SEARCH_RESULTS",
    )

    max_context_documents: int = Field(
        default=5,
        alias="MAX_CONTEXT_DOCUMENTS",
    )

    min_retrieval_score: float = Field(
        default=0.65,
        alias="MIN_RETRIEVAL_SCORE",
    )

    duplicate_similarity_threshold: float = Field(
        default=0.95,
        alias="DUPLICATE_SIMILARITY_THRESHOLD",
    )

    # ============================================
    # Chunking
    # ============================================

    chunk_size: int = Field(
        default=1200,
        alias="CHUNK_SIZE",
    )

    chunk_overlap: int = Field(
        default=200,
        alias="CHUNK_OVERLAP",
    )

    # ============================================
    # Knowledge Base
    # ============================================

    pdf_directory: str = Field(
        default="./kb/pdfs",
        alias="PDF_DIRECTORY",
    )

    markdown_directory: str = Field(
        default="./kb/markdowns",
        alias="MARKDOWN_DIRECTORY",
    )

    # ============================================
    # LLM
    # ============================================

    default_llm_provider: str = Field(
        default="azure_openai",
        alias="DEFAULT_LLM_PROVIDER",
    )

    azure_openai_api_key: str | None = Field(
        default=None,
        alias="AZURE_OPENAI_API_KEY",
    )

    azure_openai_endpoint: str | None = Field(
        default=None,
        alias="AZURE_OPENAI_ENDPOINT",
    )

    azure_openai_api_version: str = Field(
        default="2023-12-01-preview",
        alias="AZURE_OPENAI_API_VERSION",
    )

    azure_openai_deployment_name: str | None = Field(
        default=None,
        alias="AZURE_OPENAI_DEPLOYMENT_NAME",
    )

    llm_temperature: float = Field(
        default=0.1,
        alias="LLM_TEMPERATURE",
    )

    llm_timeout_seconds: int = Field(
        default=120,
        alias="LLM_TIMEOUT_SECONDS",
    )

    # ============================================
    # Azure DevOps
    # ============================================

    ado_pat: str | None = Field(
        default=None,
        alias="ADO_PAT",
    )

    ado_organization: str | None = Field(
        default=None,
        alias="ADO_ORGANIZATION",
    )

    ado_project: str | None = Field(
        default=None,
        alias="ADO_PROJECT",
    )

    # ============================================
    # Teams / Graph
    # ============================================

    microsoft_app_id: str | None = Field(
        default=None,
        alias="MICROSOFT_APP_ID",
    )

    microsoft_app_password: str | None = Field(
        default=None,
        alias="MICROSOFT_APP_PASSWORD",
    )

    # ============================================
    # Security
    # ============================================

    backend_api_key: str | None = Field(
        default=None,
        alias="BACKEND_API_KEY",
    )

    # ============================================
    # HTTP
    # ============================================

    http_timeout_seconds: int = Field(
        default=60,
        alias="HTTP_TIMEOUT_SECONDS",
    )

    # ============================================
    # Azure Key Vault (for dev mode)
    # ============================================

    azure_key_vault_name: str | None = Field(
        default=None,
        alias="AZURE_KEY_VAULT_NAME",
    )

    # ============================================
    # Computed Properties
    # ============================================

    @property
    def pdf_directory_path(self) -> Path:
        return BASE_DIR / self.pdf_directory

    @property
    def markdown_directory_path(self) -> Path:
        return BASE_DIR / self.markdown_directory


def _load_secrets_from_key_vault(vault_name: str) -> dict[str, str]:
    """
    Load secrets from Azure Key Vault.
    Returns dict of secret names to values.

    Uses DefaultAzureCredential which supports:
    - Azure CLI (az login) for local dev
    - Managed Identity for deployed environments
    - Environment variables (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID)
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        credential = DefaultAzureCredential()
        vault_url = f"https://{vault_name}.vault.azure.net/"
        client = SecretClient(vault_url=vault_url, credential=credential)

        # Map Key Vault secret names to environment variable names
        secret_mappings = {
            "QDRANT-API-KEY": "QDRANT_API_KEY",
            "AZURE-OPENAI-API-KEY": "AZURE_OPENAI_API_KEY",
            "AZURE-OPENAI-ENDPOINT": "AZURE_OPENAI_ENDPOINT",
            "ADO-PAT": "ADO_PAT",
            "MICROSOFT-APP-ID": "MICROSOFT_APP_ID",
            "MICROSOFT-APP-PASSWORD": "MICROSOFT_APP_PASSWORD",
            "BACKEND-API-KEY": "BACKEND_API_KEY",
        }

        secrets = {}
        for kv_name, env_name in secret_mappings.items():
            try:
                secret = client.get_secret(kv_name)
                secrets[env_name] = secret.value
            except Exception as e:
                # Secret doesn't exist or no permission - skip it
                log.warning(f"Could not fetch secret {kv_name} from Key Vault: {e}")
                continue

        return secrets
    except Exception as e:
        log.error(f"Failed to connect to Key Vault {vault_name}: {e}")
        return {}


@lru_cache
def get_settings() -> Settings:
    run_mode = os.getenv("RUN_MODE", "local")

    env_file = BASE_DIR / f".env.{run_mode}"

    # Load base config from .env file
    if env_file.exists():
        load_dotenv(env_file, override=False)

    # If dev mode and Key Vault is configured, pull secrets from Key Vault
    if run_mode == "dev":
        vault_name = os.getenv("AZURE_KEY_VAULT_NAME")
        if vault_name:
            log.info(f"Loading secrets from Azure Key Vault: {vault_name}")
            kv_secrets = _load_secrets_from_key_vault(vault_name)

            # Set secrets as environment variables (only if not already set)
            for key, value in kv_secrets.items():
                if key not in os.environ:
                    os.environ[key] = value

            log.info(f"Loaded {len(kv_secrets)} secrets from Key Vault")
        else:
            log.warning("Dev mode but AZURE_KEY_VAULT_NAME not set - falling back to .env.dev")

    return Settings()


settings = get_settings()