# SmartBagRAGApi - Project Context for LLMs

## Overview

A FastAPI-based Retrieval-Augmented Generation (RAG) system that indexes and queries knowledge from multiple sources: PDFs, Markdown files, Azure DevOps work items, and MS Teams threads. Uses Qdrant for vector storage, BGE embeddings, and Azure OpenAI for answer synthesis.

**Architecture:** FastAPI backend + Qdrant vector DB + Azure OpenAI + BGE embeddings

---

## Directory Structure

```
SmartBagRAGApi/
├── app/                          # Main application code
│   ├── api/                      # FastAPI routes
│   │   ├── router.py            # Main API router (aggregates all routes)
│   │   └── routes/
│   │       ├── health.py        # Health check endpoint (GET /health)
│   │       ├── ingest.py        # Ingestion endpoints (POST /ingest/ado, /ingest/teams)
│   │       └── query.py         # Query endpoints (POST /query/search, /query/answer)
│   │
│   ├── core/                     # Core infrastructure
│   │   ├── settings.py          # Environment config (Pydantic settings)
│   │   └── logging.py           # Logging configuration
│   │
│   ├── models/                   # Data models (Pydantic)
│   │   ├── payloads.py          # Qdrant payload schemas (PDFPayload, MarkdownPayload, etc)
│   │   ├── results.py           # API response models (BatchIngestionResult, etc)
│   │   ├── ado.py               # Azure DevOps models
│   │   ├── graph.py             # MS Graph/Teams models
│   │   ├── schemas.py           # Request/response schemas
│   │   ├── enums.py             # Enumerations (IngestStatus, etc)
│   │   └── constants.py         # Constants (SIMILARITY_DUPE_THRESHOLD, EMBEDDING_DIMS)
│   │
│   ├── pipelines/                # Ingestion pipelines
│   │   ├── ingest_pdf_pipeline.py       # PDF ingestion (PyMuPDF + chunking)
│   │   ├── ingest_markdown_pipeline.py  # Markdown ingestion (heading-aware chunking)
│   │   ├── ingest_ado_pipeline.py       # Azure DevOps work item ingestion
│   │   ├── ingestion_teams_pipeline.py  # MS Teams thread ingestion
│   │   └── query_pipeline.py            # Query pipeline (stub, not implemented)
│   │
│   ├── services/                 # Business logic services
│   │   ├── embedding_service.py # BGE embedding generation (bge-base-en-v1.5, 768-dim)
│   │   └── llm_service.py       # Azure OpenAI client (summarization + answer synthesis)
│   │
│   ├── stores/                   # Data access layer
│   │   └── qdrant_store.py      # Qdrant operations (upsert, search, dedup)
│   │
│   └── main.py                   # FastAPI app initialization
│
├── kb/                           # Knowledge base (ingestion sources)
│   └── markdown/handbook/        # Example markdown documents
│       ├── Benefits and Perks/
│       ├── Employment Policies/
│       ├── Hiring Documents/
│       └── ...
│
├── .env.local                    # Local environment config (development)
├── .env.dev                      # Dev environment config (Azure deployment)
├── requirements.txt              # Python dependencies
├── docker-compose.qdrant.yml     # Qdrant container setup
├── ingest_batch.py               # Batch ingestion script (PDFs + Markdown)
├── ingest.ps1                    # PowerShell wrapper for batch ingestion
├── run.ps1                       # PowerShell server startup script
├── rag_dev_plan_v4.md            # Full implementation plan/specification
└── ADO_PIPELINE_SETUP.md         # Azure DevOps CI/CD setup guide
```

---

## Component Purpose

### API Routes (`app/api/routes/`)

| File        | Purpose                         | Endpoints                                                              |
| ----------- | ------------------------------- | ---------------------------------------------------------------------- |
| `health.py` | Server health check             | GET `/health`                                                          |
| `ingest.py` | Ingestion API for ADO and Teams | POST `/ingest/ado`, POST `/ingest/teams`                               |
| `query.py`  | Search and Q&A endpoints        | POST `/query/search` (retrieval), POST `/query/answer` (LLM synthesis) |

### Ingestion Pipelines (`app/pipelines/`)

| File                          | Purpose                              | Input                      | Output                                          |
| ----------------------------- | ------------------------------------ | -------------------------- | ----------------------------------------------- |
| `ingest_pdf_pipeline.py`      | PDF text extraction and chunking     | PDF files                  | Embedded chunks in Qdrant (deterministic UUIDs) |
| `ingest_markdown_pipeline.py` | Markdown heading-aware chunking      | .md files                  | Embedded chunks preserving document structure   |
| `ingest_ado_pipeline.py`      | Azure DevOps work item serialization | ADO work items (JSON)      | Embedded work item summaries                    |
| `ingestion_teams_pipeline.py` | MS Teams thread processing           | Teams messages (Graph API) | Embedded thread summaries                       |

**Key Features:**

- **Deterministic IDs:** UUID5 hashing of source path + position (enables idempotent upserts)
- **Content hash dedup:** SHA-256 hash comparison to skip unchanged files
- **Similarity dedup:** Cosine similarity check (threshold: 0.93) to log near-duplicates
- **Chunking:** 1200 chars + 200 overlap (RecursiveCharacterTextSplitter)
- **Markdown:** Heading-aware splitting (preserves document hierarchy)

### Services (`app/services/`)

| File                   | Purpose                  | Key Functions                                                     |
| ---------------------- | ------------------------ | ----------------------------------------------------------------- |
| `embedding_service.py` | BGE embedding generation | `embed_document()`, `embed_query()` - Asymmetric retrieval scheme |
| `llm_service.py`       | Azure OpenAI integration | `summarise_thread()` (Teams), `synthesize_answer()` (Q&A)         |

**Embedding Model:** `BAAI/bge-base-en-v1.5` (768 dimensions, asymmetric retrieval)

- **Index time:** `"Represent this sentence: {text}"`
- **Query time:** `"Represent this question: {text}"`

### Data Store (`app/stores/`)

| File              | Purpose                     | Key Functions                                                                  |
| ----------------- | --------------------------- | ------------------------------------------------------------------------------ |
| `qdrant_store.py` | Qdrant vector DB operations | `upsert_point()`, `search()`, `check_near_duplicates()`, `get_payload_by_id()` |

**Collection:** `knowledge_base` (768-dim cosine similarity)
**Confidence Threshold:** 0.65 (minimum retrieval score to pass to LLM)

---

## Environment Variables

### Critical Configuration (Required for LLM features)

| Variable                       | Purpose                     | Default | Status                                   |
| ------------------------------ | --------------------------- | ------- | ---------------------------------------- |
| `AZURE_OPENAI_API_KEY`         | Azure OpenAI authentication | None    | ⚠️ NO VALIDATION - Will crash if missing |
| `AZURE_OPENAI_ENDPOINT`        | Azure OpenAI endpoint URL   | None    | ⚠️ NO VALIDATION - Will crash if missing |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Model deployment name       | None    | ⚠️ NO VALIDATION - Will crash if missing |

### Core Settings

| Variable                 | Purpose                  | Default                 | Used?                            |
| ------------------------ | ------------------------ | ----------------------- | -------------------------------- |
| `HOST`                   | Server bind address      | `0.0.0.0`               | ✅ Yes (main.py:23)              |
| `PORT`                   | Server port              | `8000`                  | ✅ Yes (main.py:24)              |
| `LOG_LEVEL`              | Logging verbosity        | `INFO`                  | ✅ Yes                           |
| `QDRANT_URL`             | Qdrant connection string | `http://localhost:6333` | ✅ Yes (qdrant_store.py:47)      |
| `QDRANT_API_KEY`         | Qdrant auth (optional)   | None                    | ✅ Yes (qdrant_store.py:54)      |
| `QDRANT_COLLECTION_NAME` | Vector collection name   | `knowledge_base`        | ✅ Yes (throughout)              |
| `EMBEDDING_MODEL`        | BGE model name           | `BAAI/bge-base-en-v1.5` | ✅ Yes (embedding_service.py:41) |
| `EMBEDDING_DEVICE`       | Torch device (cuda/cpu)  | `cuda`                  | ✅ Yes (embedding_service.py:29) |
| `LLM_TEMPERATURE`        | LLM sampling temperature | `0.1`                   | ✅ Yes (llm_service.py:92, 142)  |
| `LLM_TIMEOUT_SECONDS`    | LLM request timeout      | `120`                   | ✅ Yes (llm_service.py:93, 143)  |
| `BACKEND_API_KEY`        | Ingestion API auth       | None                    | ⚠️ Used but no validation        |

### Dead Variables (Defined but Never Used)

These are defined in `settings.py` but never referenced in code:

- `QDRANT_DISTANCE_METRIC`
- `EMBEDDING_VERSION`
- `EMBEDDING_DIMENSION` (hardcoded as 768)
- `MAX_SEARCH_RESULTS` (hardcoded in routes)
- `MAX_CONTEXT_DOCUMENTS` (hardcoded as 5 in query.py:126)
- `MIN_RETRIEVAL_SCORE` (hardcoded as 0.65 in qdrant_store.py:199)
- `DUPLICATE_SIMILARITY_THRESHOLD` (code uses 0.93 from constants.py instead of 0.95 from settings)
- `CHUNK_SIZE` (hardcoded 1200 in pipelines)
- `CHUNK_OVERLAP` (hardcoded 200 in pipelines)
- `DEFAULT_LLM_PROVIDER`
- `ADO_PAT`, `ADO_ORGANIZATION`, `ADO_PROJECT` (for future ADO integration)
- `MICROSOFT_APP_ID`, `MICROSOFT_APP_PASSWORD` (for future Teams bot integration)
- `HTTP_TIMEOUT_SECONDS`

### Hardcoded Values That Should Be Configurable

| Value                       | Location                        | Should Be                                    |
| --------------------------- | ------------------------------- | -------------------------------------------- |
| `0.65` confidence threshold | qdrant_store.py:199             | Use `MIN_RETRIEVAL_SCORE` env var            |
| `0.93` similarity threshold | constants.py:11                 | Use `DUPLICATE_SIMILARITY_THRESHOLD` env var |
| `12000` token limit         | ingestion_teams_pipeline.py:65  | Add `LLM_MAX_TOKENS` env var                 |
| `10` min message words      | ingestion_teams_pipeline.py:66  | Add `MIN_MESSAGE_WORDS` env var              |
| `50` min page chars         | ingest_pdf_pipeline.py:59       | Add `MIN_PDF_PAGE_CHARS` env var             |
| `800` Teams summary tokens  | ingestion_teams_pipeline.py:191 | Add `LLM_SUMMARY_MAX_TOKENS` env var         |
| `1000` answer tokens        | llm_service.py:141              | Add `LLM_ANSWER_MAX_TOKENS` env var          |
| Top-5 docs                  | query.py:126                    | Use existing `MAX_CONTEXT_DOCUMENTS` env var |

### .env.local Issues

⚠️ **Outdated variables in .env.local:**

- `QDRANT_HOST` and `QDRANT_PORT` - Deprecated, use `QDRANT_URL` instead
- Code uses `QDRANT_URL` but .env.local still has old format

---

## Data Flow

### Ingestion Flow (PDFs & Markdown)

```
1. ingest_batch.py → Walks kb/ directory
2. For each file:
   a. Compute SHA-256 hash
   b. Query Qdrant for existing hash (using filter by source_uri)
   c. If hash matches → Skip
   d. If new/changed:
      - Extract text (PyMuPDF for PDF, markdown-it-py for MD)
      - Chunk text (1200 chars, 200 overlap)
      - Generate deterministic UUID (UUID5 from path + position)
      - Embed chunks (BGE with "Represent this sentence:" prefix)
      - Check for near-duplicates (cosine > 0.93)
      - Upsert to Qdrant with full metadata
```

### Query Flow (Search Mode)

```
1. User query → POST /query/search
2. Embed query (BGE with "Represent this question:" prefix)
3. Qdrant vector search (top-k, optional filters)
4. Check confidence (top score >= 0.65)
5. Return raw results with snippets
```

### Query Flow (Answer Mode)

```
1. User query → POST /query/answer
2. Embed query → Qdrant search (same as search mode)
3. If confidence < 0.65 → Return "no relevant info"
4. If confidence met:
   a. Take top 5 results
   b. Extract full content (not snippets)
   c. Format context with metadata (title, source_type, content)
   d. Send to Azure OpenAI with synthesis prompt
   e. Return synthesized answer + sources
```

---

## Key Design Decisions

### 1. Deterministic Point IDs

**Why:** Enables idempotent upserts without pre-delete logic.

**Implementation:** UUID5 with DNS namespace:

- Markdown: `uuid5(DNS, "md::path::section-slug::chunk-index")`
- PDF: `uuid5(DNS, "pdf::path::page::chunk-index")`
- ADO: `uuid5(DNS, "ado::work-item-id")`
- Teams: `uuid5(DNS, "teams::thread-id")`

**Previous Approach:** Used custom string IDs (e.g., `"md::path::section::0"`), but Qdrant requires UUIDs or unsigned integers.

### 2. Two-Stage Deduplication

**Stage 1 - Content Hash:** SHA-256 comparison before embedding (saves compute)
**Stage 2 - Similarity Check:** Cosine similarity > 0.93 (logs near-duplicates, doesn't auto-delete)

### 3. Asymmetric Embedding (BGE Requirement)

BGE models require different prefixes for indexing vs querying:

- **Index:** `"Represent this sentence: {text}"`
- **Query:** `"Represent this question: {text}"`

⚠️ **Current Issue:** `query.py:58` calls `embed_document()` instead of `embed_query()` (minor inconsistency but works)

### 4. Graceful LLM Failure

Both Teams and ADO ingestion pipelines fall back to raw/serialized text if LLM summarization fails.

- `is_summary=True` → AI-generated summary
- `is_summary=False` → Raw text fallback

### 5. Payload-Based Probe (Not ID-Based)

**Old approach:** Probed for specific chunk ID (e.g., `root::0` for markdown)
**Problem:** Files starting with headings don't have a "root::0" chunk
**New approach:** Uses Qdrant scroll filter by `source_uri` to find ANY chunk from the file

---

## Development Workflow

### 1. Start Qdrant

```powershell
docker-compose -f docker-compose.qdrant.yml up -d
```

**Storage:** Data persists to `./qdrant_storage/`

### 2. Start FastAPI Server

```powershell
./run.ps1 local   # Uses .env.local, port 8844 (configurable)
```

Server runs at: `http://localhost:8844`
Swagger UI: `http://localhost:8844/docs`

### 3. Ingest Knowledge Base

```powershell
./ingest.ps1 local   # Batch ingest PDFs + Markdown
```

Ingests from `kb/pdf/` and `kb/markdown/` directories.

### 4. Query the System

**Search mode (raw retrieval):**

```powershell
curl -X POST http://localhost:8844/query/search `
  -H "Content-Type: application/json" `
  -d '{"query": "What is the vacation policy?", "top_k": 5}'
```

**Answer mode (LLM synthesis):**

```powershell
curl -X POST http://localhost:8844/query/answer `
  -H "Content-Type: application/json" `
  -d '{"query": "What is the vacation policy?", "top_k": 5}'
```

---

## Testing

### Health Check

```bash
curl http://localhost:8844/health
```

### Search Endpoint

- Returns raw retrieval results
- No LLM call
- Fast, useful for debugging retrieval quality

### Answer Endpoint

- Requires valid Azure OpenAI credentials
- Passes full document content to LLM
- Returns synthesized answer with citations

### Manual Testing Checklist

1. ✅ Health endpoint responds
2. ✅ Qdrant container running
3. ✅ Search returns relevant results (confidence >= 0.65)
4. ✅ Answer synthesis works with citations
5. ✅ Low confidence queries return "no info" message
6. ✅ Ingestion skips unchanged files (hash dedup)

---

## Known Issues & TODOs

### Critical Issues

1. **No LLM credential validation on startup**
   - Server starts successfully even with invalid/missing Azure OpenAI creds
   - Only fails when answer endpoint is called
   - **Fix:** Add startup validation in `settings.py` or `main.py`

2. **BACKEND_API_KEY returns 500 instead of 401**
   - If API key is not set, ingestion endpoints return HTTP 500
   - Should return HTTP 401 Unauthorized
   - **Fix:** Add proper null check in `ingest.py:28-33`

3. **Outdated .env.local**
   - Uses deprecated `QDRANT_HOST`/`QDRANT_PORT` variables
   - Code uses `QDRANT_URL` but .env.local doesn't
   - **Fix:** Update .env.local to use `QDRANT_URL`

### Medium Priority

4. **Dead environment variables**
   - 16 env vars defined but never used
   - Causes confusion about what's actually configurable
   - **Fix:** Remove unused vars or implement their usage

5. **Hardcoded configuration**
   - 9 magic numbers that should be env vars
   - Cannot tune without code changes
   - **Fix:** Extract to settings.py with env var backing

6. **Inconsistent thresholds**
   - Settings defines `DUPLICATE_SIMILARITY_THRESHOLD=0.95`
   - Code uses `0.93` from constants.py
   - **Fix:** Use settings value consistently

### Low Priority

7. **Query endpoint uses `embed_document()` instead of `embed_query()`**
   - Minor inconsistency with BGE best practices
   - Still works but suboptimal
   - **Fix:** Change `query.py:58` to use `embed_query()`

8. **No file serving endpoint**
   - `source_uri` in responses is a file path, not a URL
   - Cannot navigate to source documents via HTTP
   - **Fix:** Add optional file serving route (security consideration)

---

## Architecture Notes

### Why FastAPI?

- Async support for concurrent ingestion/queries
- Automatic OpenAPI/Swagger documentation
- Pydantic integration for type safety
- Fast performance for production deployment

### Why Qdrant?

- Native vector search with payload filtering
- Supports both in-memory and persistent storage
- Simple Docker deployment
- Good Python client library

### Why BGE Embeddings?

- SOTA open-source embedding model
- 768 dimensions (good quality/size tradeoff)
- Asymmetric retrieval support (optimized for search)
- Can run locally (no API costs)

### Why Azure OpenAI?

- Enterprise-grade reliability and security
- Easy integration with Azure ecosystem
- Supports GPT-4 models for high-quality synthesis
- Can switch to Anthropic Claude if needed (anthropic SDK already installed)

---

## Future Enhancements

### Short-term (Next Milestone)

- [ ] Add startup validation for critical env vars
- [ ] Fix BACKEND_API_KEY authentication (return 401 not 500)
- [ ] Update .env.local to use QDRANT_URL
- [ ] Implement usage of existing env vars (MIN_RETRIEVAL_SCORE, MAX_CONTEXT_DOCUMENTS, etc)
- [ ] Add proper error handling for LLM failures

### Medium-term (Post-MVP)

- [ ] Teams bot integration (message action for thread ingestion)
- [ ] ADO webhook for auto-ingestion on work item changes
- [ ] Cross-encoder reranking (improve retrieval quality)
- [ ] Hybrid BM25 + dense search
- [ ] Token/cost tracking for LLM calls
- [ ] Streaming LLM responses

### Long-term (Future)

- [ ] Multi-tenancy support
- [ ] Alternative LLM providers (Anthropic Claude, Ollama)
- [ ] Web UI for knowledge base management
- [ ] Advanced analytics (query logs, retrieval quality metrics)
- [ ] Fine-tuned embedding models

---

## Dependencies

See `requirements.txt` for full list. Key dependencies:

| Package                  | Version | Purpose                 |
| ------------------------ | ------- | ----------------------- |
| fastapi                  | 0.109.0 | Web framework           |
| uvicorn                  | 0.27.0  | ASGI server             |
| qdrant-client            | 1.7.1   | Vector database client  |
| sentence-transformers    | 2.2.2   | BGE embedding model     |
| openai                   | 1.3.7   | Azure OpenAI SDK        |
| PyMuPDF (fitz)           | 1.23.8  | PDF text extraction     |
| langchain-text-splitters | 0.0.1   | Document chunking       |
| markdown-it-py           | 3.0.0   | Markdown parsing        |
| tiktoken                 | 0.5.2   | Token counting (OpenAI) |
| pydantic-settings        | 2.1.0   | Environment config      |

---

## Contact & Support

- **Plan Document:** See `rag_dev_plan_v4.md` for full specification
- **ADO Setup:** See `ADO_PIPELINE_SETUP.md` for CI/CD pipeline setup
- **Issues:** Check Known Issues section above

---

**Last Updated:** 2026-05-09
**Version:** 1.0 (MVP Complete)
