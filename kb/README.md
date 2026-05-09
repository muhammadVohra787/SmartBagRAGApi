# Knowledge Base Directory

This directory contains the source documents for the RAG knowledge base.

## Structure

- `pdf/` - PDF documents (technical docs, manuals, reports)
- `markdown/` - Markdown files (guides, wiki pages, structured docs)

## Ingestion

### Using PowerShell (Recommended)

```powershell
# Uses .env.local (default)
.\ingest.ps1

# Uses .env.dev
.\ingest.ps1 -Mode dev
```

### Direct Python

```bash
# Uses .env.local (default)
python ingest_batch.py

# Uses .env.dev
$env:RUN_MODE="dev"
python ingest_batch.py
```

The script:

- Loads settings from `.env.{RUN_MODE}` (same as run.ps1)
- Walks `kb/pdf/` and `kb/markdown/` recursively
- Computes SHA-256 hash of each file
- Compares against stored hash in Qdrant
- **Skips unchanged files** (no re-embedding needed)
- **Ingests new or modified files only**
- Logs near-duplicates (similarity > 0.93) without deleting

### What Needs to Be Running?

**Only Qdrant** — that's it!

- ✅ **Qdrant** must be running (Docker or VM)
- ❌ **FastAPI app** is NOT used for KB ingestion

**Note**: The FastAPI app has separate API routes for ADO/Teams ingestion (`/api/ingest/ado`, `/api/ingest/teams`). Those are completely independent from this batch script. KB files are NEVER ingested through the API.

## File Organization Tips

- Use descriptive filenames - they appear in search results
- PDFs: one doc per file (multi-page is fine)
- Markdown: split large guides into separate files by topic
- Avoid duplicating content between files (near-duplicate detection will flag it)

## Metadata

Every ingested chunk includes:

- `source_uri` - file path
- `content_hash` - SHA-256 for deduplication
- `embedding_model` - `bge-base-en-v1.5`
- `embedding_version` - `1` (bump when model changes)
- `source_priority` - `1` (PDFs and Markdown are highest priority)

## Re-ingestion

To force re-ingestion of a file:

1. Make any change to the file (even a typo fix)
2. Commit and push (CI) or run `python ingest_batch.py` (local)

The hash comparison will detect the change and re-process the file.

## Deletion

To remove a document from the knowledge base:

1. Delete the file from this directory
2. Run the cleanup script: `python scripts/cleanup_deleted.py` (TODO: create this script)
