# Azure DevOps Pipeline Setup

## 1. Configure Key Vault Access for ADO

### Get your ADO agent's Managed Identity

1. Go to **Azure DevOps** → **Project Settings** → **Service Connections**
2. Find the **Azure Resource Manager** connection used by your pipeline
3. Note the **Service Principal Object ID**

### Grant Key Vault access to the agent

```bash
az keyvault set-policy \
  --name your-keyvault-name \
  --object-id <ado-agent-object-id> \
  --secret-permissions get list
```

### Create ADO Variable Group

1. Go to **Pipelines** → **Library** → **+ Variable group**
2. Name: `rag-kb-secrets`
3. Add ONE variable:
   - `AZURE_KEY_VAULT_NAME` = `your-keyvault-name` (not secret)
4. Click **Save**

**No secrets needed in ADO** - the agent pulls them from Key Vault using its Managed Identity.

> **Note**: Make sure the 7 secrets are already in Key Vault:
> - `QDRANT-API-KEY`
> - `AZURE-OPENAI-API-KEY`
> - `AZURE-OPENAI-ENDPOINT`
> - `ADO-PAT`
> - `MICROSOFT-APP-ID`
> - `MICROSOFT-APP-PASSWORD`
> - `BACKEND-API-KEY`

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
- ✅ Qdrant only (accessed via Key Vault credentials)
- ❌ FastAPI app NOT used

**How it works:**
1. Pipeline sets `RUN_MODE=dev`
2. Loads `.env.dev` from repo (gets `AZURE_KEY_VAULT_NAME`)
3. Uses Managed Identity to pull secrets from Key Vault
4. Runs ingestion with secrets from Key Vault

**Note**: This is for KB files (PDF/Markdown) only. ADO and Teams ingestion use separate API routes.

## 4. Trigger in ADO (Manual)

To run ingestion from Azure DevOps:

1. Go to **Pipelines** → Select `azure-pipelines-ingest`
2. Click **Run pipeline** button (top-right)
3. Select branch (usually `main`)
4. Click **Run**

The pipeline will:

- Install Python and dependencies (including `azure-keyvault-secrets`)
- Set `RUN_MODE=dev` and `AZURE_KEY_VAULT_NAME`
- Pull secrets from Key Vault using Managed Identity
- Run `python ingest_batch.py --ci`
- Exit with error if any file fails

## 5. When to Run

Run ingestion (local or ADO) after:

- Adding new PDFs/Markdown files to `kb/`
- Updating existing documentation
- Changing embedding model (re-ingest everything)

## Local vs ADO

| Method                    | When to Use                                          | Authentication           |
| ------------------------- | ---------------------------------------------------- | ------------------------ |
| **Local**: `.\ingest.ps1` | Dev testing, quick updates, local validation         | Azure CLI (`az login`)   |
| **ADO Pipeline**          | Production ingestion, team coordination, audit trail | Managed Identity (automatic) |

Both use the same script, load from `.env.dev`, and pull secrets from Key Vault.
