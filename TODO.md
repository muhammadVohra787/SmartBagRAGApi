Focusing on the FastAPI backend only, here's the alignment status:

---

## ✅ API Endpoints — Fully Implemented

| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /health` | ✅ Working | Health check |
| `POST /query/search` | ✅ Working | Raw retrieval results |
| `POST /query/answer` | ✅ Working | LLM-synthesized answers |
| `POST /ingest/ado` | ✅ Working | ADO work item ingestion |
| `POST /ingest/teams` | ✅ Working | Teams thread ingestion |

---

## ❌ Missing API Components

### **1. Authentication Middleware** — Not Integrated

**What's missing:**
- `files/auth.py` exists but not integrated into `app/core/`
- No API key validation dependency for ingestion routes
- Returns HTTP 500 instead of 401 on missing/invalid API key

**What the plan requires:**
- API key validation on all ingestion endpoints **before any processing**
- Startup validation for `BACKEND_API_KEY` env var
- Proper 401 Unauthorized responses

**Fix needed:**
```python
# app/core/auth.py (from files/auth.py)
# Then add to ingest routes:
@router.post("/ingest/ado", dependencies=[Depends(verify_api_key)])
```

---

### **2. Startup Validation** — Missing

**What's missing:**
- No validation that critical env vars are set (Azure OpenAI credentials)
- Server starts successfully even if `AZURE_OPENAI_API_KEY` is invalid
- Only fails when `/query/answer` is called

**What the plan requires:**
- Fail fast on startup if critical credentials missing
- Validate Azure OpenAI connection before accepting requests

**Fix needed:**
```python
# In app/main.py startup event
@app.on_event("startup")
async def validate_environment():
    # Check Azure OpenAI credentials
    # Check BACKEND_API_KEY is set
    # Test Qdrant connection
```

---

### **3. Metadata Fields** — Need Verification

**What might be missing:**

From the plan, every document must have:
- `embedding_version` (integer) — for progressive re-embedding
- `source_priority` (integer) — for tie-breaking (pdf=1, markdown=1, ado=2, teams=3)

**Action needed:** Verify these fields exist in payload models and are populated during ingestion.

---

### **4. Qdrant Payload Indexes** — Not Created

**What's missing:**
- No code to create payload indexes on collection setup
- Filtered queries will be slow without indexes

**What the plan requires:**
```python
# Indexes needed:
- source_type (keyword)
- work_item_id (integer)
- thread_id (keyword)
- filename (keyword)
- embedding_version (integer)
- work_item_state (keyword)
- tags (keyword)
```

**Fix needed:**
- Add index creation function in `qdrant_store.py`
- Call on collection initialization

---

### **5. Configuration Issues** — Cleanup Needed

**Problems:**

| Issue | Impact |
|-------|--------|
| 16 dead env vars defined but unused | Confusing API |
| 9 hardcoded values that should be configurable | Can't tune without code changes |
| `query.py` uses `embed_document()` instead of `embed_query()` | Suboptimal embeddings |
| Inconsistent thresholds (0.93 vs 0.95) | Constants.py vs settings.py conflict |

**Files to clean up:**
- `app/core/settings.py` — remove unused vars
- `app/models/constants.py` — use settings.py values instead
- `app/api/routes/query.py:58` — fix embedding function call

---

### **6. Error Handling** — Incomplete

**What's missing:**
- Graceful handling when LLM fails (partially implemented)
- Proper HTTP status codes for various failure modes
- Structured error responses

**Current issues:**
- Missing API key → 500 (should be 401)
- Invalid query → varies
- LLM timeout → needs consistent handling

---

## 🔧 API-Only Action Plan

### **Priority 1: Security & Validation**
1. ✅ Integrate `files/auth.py` → `app/core/auth.py`
2. ✅ Add API key dependency to ingestion endpoints
3. ✅ Add startup validation for critical env vars

### **Priority 2: Metadata & Indexes**
4. ✅ Verify `embedding_version` and `source_priority` in payload models
5. ✅ Create Qdrant payload indexes initialization function
6. ✅ Add index creation to collection setup

### **Priority 3: Configuration Cleanup**
7. ✅ Remove 16 dead env vars from `settings.py`
8. ✅ Move 9 hardcoded values to env vars
9. ✅ Fix query embedding inconsistency
10. ✅ Consolidate threshold constants

### **Priority 4: Error Handling**
11. ✅ Standardize error responses
12. ✅ Add proper HTTP status codes
13. ✅ Improve LLM failure handling

---

## Quick Wins (Low Effort, High Impact)

1. **Fix auth.py integration** — 15 minutes, prevents 500 errors
2. **Add startup validation** — 30 minutes, fail fast instead of runtime errors
3. **Fix query embedding call** — 2 minutes, better retrieval quality
4. **Create Qdrant indexes** — 20 minutes, much faster filtered queries

Want me to start with any of these?