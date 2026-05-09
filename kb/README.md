# Knowledge Base Directory

This directory contains the source documents for the RAG knowledge base.

## Structure

- `pdf/` - PDF documents (technical docs, manuals, reports)
- `markdown/` - Markdown files (guides, wiki pages, structured docs)

## Ingestion

### Local Ingestion

```bash
# From project root
python ingest_batch.py
```

The script:

- Walks `kb/pdf/` and `kb/markdown/` recursively
- Computes SHA-256 hash of each file
- Compares against stored hash in Qdrant
- **Skips unchanged files** (no re-embedding needed)
- **Ingests new or modified files only**
- Logs near-duplicates (similarity > 0.93) without deleting

### CI/CD Ingestion

The GitHub Actions workflow `.github/workflows/ingest.yml` triggers on:

- Push to `main` branch when files in `kb/` change
- Manual workflow dispatch

CI mode (`--ci` flag) exits with code 1 if any file fails ingestion.

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
