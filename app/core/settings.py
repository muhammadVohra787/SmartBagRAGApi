from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


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
    # Computed Properties
    # ============================================

    @property
    def pdf_directory_path(self) -> Path:
        return BASE_DIR / self.pdf_directory

    @property
    def markdown_directory_path(self) -> Path:
        return BASE_DIR / self.markdown_directory


@lru_cache
def get_settings() -> Settings:
    run_mode = os.getenv("RUN_MODE", "local")

    env_file = BASE_DIR / f".env.{run_mode}"

    if env_file.exists():
        load_dotenv(env_file, override=False)

    return Settings()


settings = get_settings()