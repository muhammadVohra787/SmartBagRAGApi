# Azure DevOps Pipeline Setup

## 1. Create Variable Group

In your ADO project:

1. Go to **Pipelines** → **Library** → **+ Variable group**
2. Name: `rag-kb-secrets`
3. Add these variables:

| Variable         | Value                    | Secret? |
| ---------------- | ------------------------ | ------- |
| `QDRANT_URL`     | `http://your-vm-ip:6333` | No      |
| `QDRANT_API_KEY` | Your Qdrant API key      | ✅ Yes  |

4. Click **Save**

> **Note**: OpenAI keys are NOT needed for batch ingestion. PDF/Markdown ingestion only uses embeddings (sentence-transformers locally). OpenAI is only used for ADO/Teams summarization via API routes.

## 2. Create Pipeline

1. Go to **Pipelines** → **New pipeline**
2. Select your repo source (Azure Repos Git)
3. Choose **Existing Azure Pipelines YAML file**
4. Select `/azure-pipelines-ingest.yml`
5. Click **Save** (don't run yet)

## 3. Run Locally (Same as run.ps1)

### Using PowerShell (Recommended)

```powershell
# Uses .env.local
.\ingest.ps1

# Uses .env.dev
.\ingest.ps1 -Mode dev
```

### Direct Python

```bash
# Uses .env.local (default)
python ingest_batch.py

# Uses .env.dev
RUN_MODE=dev python ingest_batch.py
```

**What needs to be running?**
- ✅ Qdrant only
- ❌ FastAPI app NOT used

The script loads settings from `.env.{RUN_MODE}` automatically (same pattern as `run.ps1`).

**Note**: This is for KB files (PDF/Markdown) only. ADO and Teams ingestion use separate API routes and have nothing to do with this pipeline.

## 4. Trigger in ADO (Manual)

To run ingestion from Azure DevOps:

1. Go to **Pipelines** → Select `azure-pipelines-ingest`
2. Click **Run pipeline** button (top-right)
3. Select branch (usually `main`)
4. Click **Run**

The pipeline will:

- Install Python and dependencies
- Run `python ingest_batch.py --ci`
- Exit with error if any file fails

## 5. When to Run

Run ingestion (local or ADO) after:

- Adding new PDFs/Markdown files to `kb/`
- Updating existing documentation
- Changing embedding model (re-ingest everything)

## Local vs ADO

| Method                      | When to Use                                          |
| --------------------------- | ---------------------------------------------------- |
| **Local**: `.\ingest.ps1`   | Dev testing, quick updates, local validation         |
| **ADO Pipeline**            | Production ingestion, team coordination, audit trail |

Both use the same script and load from `.env` files (local) or ADO variable group (pipeline).
