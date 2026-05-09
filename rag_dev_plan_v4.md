# RAG Chatbot — Dev Plan (v4)

---

## What We're Building

A chatbot and knowledge search tool added to MS Teams. The knowledge base is built from four sources: Teams channel threads, Azure DevOps work items, PDFs, and Markdown files. The backend is FastAPI, the vector store is Qdrant, both hosted on a VM.

The system has two distinct modes sharing the same retrieval pipeline underneath:

**Search mode** — returns raw retrieval results: title, source, snippet, similarity score, direct link. No LLM involved. User sees exactly what was found.

**Q&A mode** — takes the same retrieval results and passes them to the LLM for synthesis into a grounded, cited answer.

This separation matters for three reasons. First, some users want to find documents, not get a summary. Second, search mode works even if the LLM is down. Third, and most importantly early on, search mode lets you see exactly what the retrieval pipeline is returning — bad chunking, poor embeddings, duplicates, and metadata gaps are immediately visible. Without it, everything is hidden behind the LLM and debugging becomes guesswork.

---

## MVP Scope

**In scope for MVP:**

- FastAPI backend on a VM with Qdrant
- ADO work item ingestion via button in the ADO extension (direct serialisation, no AI summary)
- PDF and Markdown ingestion via a repo batch script
- `/query/search` endpoint — raw retrieval results
- `/query/answer` endpoint — retrieval + LLM synthesis
- Teams bot defaulting to Q&A mode, with `/search` command for raw results
- ID-based and similarity-based dedup with deterministic point IDs
- Embedding version metadata and source priority on every document
- Citation metadata on every document
- Retrieval confidence threshold to prevent low-quality answers

**Out of scope for MVP:**

- Teams thread ingestion (post-MVP)
- AI summary for ADO items (post-MVP)
- ADO webhook auto-ingest (post-MVP)
- Cross-encoder reranking (post-MVP)
- Hybrid BM25 sparse search (post-MVP)
- Async ingestion queue (post-MVP)

---

## Resources to Provision

### Azure

**App Registration (Azure AD)**
The identity the bot uses to talk to Microsoft services. Produces a Client ID and a Client Secret. Graph API permissions are declared here and require admin consent from the tenant.

Graph permissions needed:

- `ChannelMessage.Read.All` — read Teams thread messages
- `Team.ReadBasic.All` — enumerate teams and channels
- `User.Read.All` — resolve participant display names

> Admin consent is the longest lead-time item. Raise the request to your Azure admin as early as possible.

**Azure Bot Service**
Links the App Registration to your backend endpoint (`/api/messages`). You enable the Teams channel here.

**Teams App (Developer Portal)**
The Teams app package. Declares the bot and the message action. Sideloaded to a team for dev testing, or submitted to Teams Admin Center for org-wide approval.

> The "Register in KB" trigger for Teams threads uses a **message action** — it appears in the `...` context menu when a user right-clicks (or long-presses on mobile) any message in a channel thread. This is the only native Teams mechanism for adding a per-message action without a separate bot card.

### Azure DevOps

**ADO Marketplace Publisher Account**
Required to upload the VSIX extension. Set visibility to Private.

**Personal Access Token (PAT)**
Scopes: Work Items Read/Write, Service Hooks Read/Write. Stored as an environment variable on the backend VM, never in code.

**ADO Service Hook** _(post-MVP)_
Triggers automatic ingestion when a work item moves to Resolved or Closed.

**Backend API Key**
The VSIX extension calls your FastAPI directly from the browser. It sends a static API key in the request header. Generate one, store it in the extension config and the backend environment.

### VM (EC2 or Azure VM)

One VM. Minimum 4 vCPU / 16 GB RAM. Ubuntu 22.04 LTS. 128 GB SSD data disk.

Services on the VM:

- FastAPI (behind Nginx)
- Qdrant (internal only — never exposed publicly)
- Nginx (reverse proxy, HTTPS)

Only port 443 is open to the internet.

**Security baseline — add from day one:**

- Nginx request size limit (e.g. 10 MB max body) to prevent accidental large payload uploads
- Basic rate limiting at the Nginx level (e.g. 10 requests/second per IP) on ingestion endpoints
- API key validation on all ingestion endpoints before any processing happens

---

## Libraries

### Backend Core

**FastAPI** — async web framework.

**Uvicorn** — ASGI server. Run with 4 workers in production behind Nginx.

**Pydantic** — request/response validation and data models.

**httpx** — async HTTP client for all outbound calls: MS Graph, ADO REST, LLM providers.

### Ingestion — Teams Threads _(post-MVP)_

**botbuilder-core + botbuilder-integration-aiohttp** — Bot Framework SDK for Python. Handles incoming Teams activities and sends replies.

**msgraph-sdk-python** — fetches thread messages, channel info, and user details from Microsoft Graph.

**html2text** — converts Teams message HTML to clean plain text. Teams message bodies arrive as HTML. This strips tags cleanly: `<at>` mentions keep the display name, `<a>` links keep visible text and drop URLs, `<p>` and `<br>` become line breaks, `<attachment>` and `<img>` are stripped. Do not use `bleach` — it is deprecated and unmaintained.

**tiktoken** — token counting before the LLM summarisation call. Trims threads that exceed the model's context window.

### Ingestion — ADO

**azure-devops** — Microsoft's official Python SDK for ADO.

**msrest / azure-identity** — PAT-based authentication helpers.

### Ingestion — PDF

**PyMuPDF (fitz)** — PDF text extraction. Fast, handles multi-column layouts, gives page-level metadata. Use this for all PDFs in MVP.

> pdfplumber is better for table-heavy PDFs but requires knowing in advance which PDFs have tables. That's operationally unclear and premature for MVP. Use PyMuPDF only. Add pdfplumber selectively post-MVP if table extraction becomes a real problem.

**LangChain (RecursiveCharacterTextSplitter)** — chunks extracted text into segments of 1,200 characters with 200 character overlap. Character-based sizing is simpler to implement for MVP than token-based. Switch to token-based later if needed.

### Ingestion — Markdown

**markdown-it-py** — Markdown parser that produces a token stream (headings, code blocks, paragraphs). Used to identify heading boundaries before chunking so the document structure is preserved.

**LangChain (MarkdownHeaderTextSplitter + RecursiveCharacterTextSplitter)** — splits on heading boundaries first, then chunks within each section at 1,200 characters / 200 overlap. The heading text is included at the top of each chunk so it is always present in the embedding.

### Embedding

**sentence-transformers** — runs the embedding model locally on the VM.

Model: `BAAI/bge-base-en-v1.5` — **768 dimensions**.

> Dimension note: bge-small = 384 dims, bge-base = 768 dims, bge-large = 1024 dims. These are not interchangeable. The Qdrant collection vector size must be set to match the model exactly. If you upgrade from bge-base to bge-large you must re-embed everything and recreate the collection with size 1024. Embedding version metadata (see below) makes this migration manageable.

### Storage

**qdrant-client** — official Qdrant Python client. Handles upserts, deletes, filtered searches, and payload index management.

### LLM

**anthropic** — SDK for Claude. Used for Teams thread summarisation and Q&A answer generation.

**openai** — keep available as a fallback. Both sit behind a single abstraction so switching is one config change.

---

## Qdrant Collection Setup

### Vector Config

Collection name: `knowledge_base`

Dense vector size: `768` (matches bge-base-en-v1.5). If you switch models, this changes.

### Payload Indexes

Create these indexes on the collection before any ingestion. They dramatically improve filter performance at query time — without them, Qdrant scans every document for every filtered query.

Indexes needed:

- `source_type` — keyword
- `work_item_id` — integer
- `thread_id` — keyword
- `filename` — keyword
- `embedding_version` — integer
- `work_item_state` — keyword
- `tags` — keyword

---

## Deterministic Point IDs

Every document stored in Qdrant gets a deterministic UUID based on its source and position. The UUID is generated using UUID5 (namespace-based hashing) from a logical string ID, ensuring the same input always produces the same UUID. This means an upsert naturally replaces an existing entry without needing a pre-delete scan.

Logical ID format per source type (hashed to UUID5):

- ADO work item: `ado::{work_item_id}` → UUID5 → e.g. `"3d4f5e6a-7b8c-5d9e-af01-2b3c4d5e6f7a"`
- Teams thread: `teams::{thread_id}` → UUID5 → e.g. `"8a9b0c1d-2e3f-5a4b-9c5d-6e7f8a9b0c1d"`
- PDF chunk: `pdf::{filepath}::{page_number}::{chunk_index}` → UUID5
- Markdown chunk: `md::{filepath}::{section_slug}::{chunk_index}` → UUID5

**Why UUIDs?** Qdrant requires point IDs to be either unsigned integers or UUIDs. Custom string formats are not supported. UUID5 gives us deterministic IDs (same input = same UUID) while meeting Qdrant's requirements.

With deterministic IDs, the delete-then-upsert pattern from earlier versions of this plan is unnecessary. Upserting with the same ID overwrites the existing entry automatically.

---

## Deduplication Strategy

### Same Document (ID Match)

Because IDs are deterministic, re-ingesting the same document with the same ID automatically replaces it in Qdrant. The only question is whether to bother re-ingesting at all if the content hasn't changed.

Before embedding and upserting, compute a `content_hash` (SHA-256 of the file contents for PDF/MD, or a combination of revision number and message count for ADO/Teams). Compare against the stored `content_hash` in Qdrant. If identical, skip. If different, proceed — the upsert will overwrite.

> Use SHA-256 of file contents for PDFs and Markdown, not last-modified timestamp. Timestamps change across systems and CI environments without any content change. Content hash is deterministic and safe.

### Different Document, High Similarity

After embedding new content, search Qdrant for the top result and check its score. If a **different** document ID scores above 0.93 cosine similarity, log a near-duplicate warning with both IDs and scores. Do not automatically delete or skip — two genuinely distinct documents that happen to overlap are both worth keeping. This is a human review signal, not an automatic action.

> Validate the 0.93 threshold after the first real ingestion batch. Adjust if you see false positives (unrelated items flagged) or false negatives (obvious duplicates missed).

---

## Embedding Version Metadata

Every document stored in Qdrant includes:

- `embedding_model`: `"bge-base-en-v1.5"`
- `embedding_version`: `1`

When the model is upgraded, bump `embedding_version` to 2. The re-embedding migration script filters Qdrant for all documents where `embedding_version < 2` and reprocesses them. Documents already on the new model are untouched.

---

## Source Priority Metadata

Every document also includes a `source_priority` integer. This is used later for reranking tie-breaking and answer synthesis — when two documents score similarly, the one with lower priority number wins. Set this now because adding it later requires reindexing everything.

Priority values:

- `pdf`: 1 — authoritative documentation
- `markdown`: 1 — authoritative documentation
- `ado`: 2 — work items, closer to ground truth on specific features
- `teams`: 3 — discussions, useful context but more informal and potentially outdated

---

## Citation & Source Metadata

Every document carries a consistent metadata payload for answer citation and source navigation.

**On every document regardless of source:**

- `source_type` — `teams_thread`, `ado_work_item`, `pdf`, `markdown`
- `source_uri` — direct URL or file path
- `title` — human-readable label
- `author` — who created it
- `date` — creation or last-updated date
- `content_hash` — SHA-256 for dedup
- `is_summary` — true if AI-generated summary, false if raw/serialised text
- `embedding_model` — model name string
- `embedding_version` — integer
- `source_priority` — integer

**ADO only:**

- `work_item_id`
- `work_item_type` — Bug, Feature, Task, Epic
- `work_item_state` — Active, Resolved, Closed
- `tags` — list
- `participants` — assignee and commenters

**Teams only:**

- `thread_id`
- `channel_name`
- `participants` — all contributors

**PDF only:**

- `filename`
- `page_number`
- `total_pages`
- `chunk_index`

**Markdown only:**

- `filename`
- `section_heading`
- `heading_level`
- `chunk_index`

---

## Teams HTML Sanitisation _(needed for post-MVP Teams ingestion)_

Teams message bodies arrive from the Graph API as HTML. They must be cleaned before any processing.

**Library: html2text**

Run immediately after fetching messages from Graph, before anything else.

Configure it to: drop link URLs (keep visible text only), flatten bold/italic to plain text, convert block elements to line breaks.

**Handle these edge cases before html2text runs:**

- Filter out system messages: only process messages where `messageType == "message"`. Ignore `systemEventMessage` (join/leave, call events).
- After cleaning, discard messages shorter than 10 words — these are acknowledgements or reactions.
- Messages that are only an attachment with no text body will produce an empty string after cleaning. Skip them.

---

## Ingestion Pipelines

### PDF Ingestion

**Trigger:** A script runs against the designated folder in the repo. Run manually or in CI when the repo changes. Batch only — not real-time.

**Pipeline:**

1. Walk the repo folder and collect all PDF files.
2. For each file, compute SHA-256 of the file contents. Query Qdrant for an existing document with this filename. If the stored `content_hash` matches, skip the file.
3. Open the PDF with PyMuPDF. Extract text page by page.
4. For each page, chunk the extracted text using RecursiveCharacterTextSplitter at 1,200 characters with 200 character overlap.
5. Assign deterministic IDs: `pdf::{filepath}::{page}::{chunk_index}`.
6. Embed each chunk using bge-base-en-v1.5.
7. Run the Stage 2 similarity check. Log near-duplicates from different source IDs — do not auto-delete.
8. Upsert each chunk. Qdrant overwrites any existing entry with the same ID.
9. Store citation metadata: filename, page number, chunk index, total pages, file path as source URI, content hash.

---

### Markdown Ingestion

**Trigger:** Same script as PDF. Batch only.

**Pipeline:**

1. Collect all `.md` files in the designated folder.
2. Compute SHA-256 of file contents. Compare against stored `content_hash`. Skip if unchanged.
3. Parse the file with markdown-it-py to extract the heading structure.
4. Split on heading boundaries using MarkdownHeaderTextSplitter. Each section starts with its heading text included at the top of the chunk — this is important for retrieval quality.
5. Chunk within each section at 1,200 characters / 200 overlap using RecursiveCharacterTextSplitter if the section is too long.
6. Keep code blocks attached to their surrounding prose. A fenced code block without context is nearly useless for retrieval.
7. Assign deterministic IDs: `md::{filepath}::{section_slug}::{chunk_index}`.
8. Embed each chunk.
9. Stage 2 similarity check. Log near-duplicates, do not auto-delete.
10. Upsert with citation metadata: filename, section heading, heading level, chunk index, file path as source URI, content hash.

---

### ADO Work Item Ingestion

**Trigger:** User clicks "Register in KB" in the ADO work item toolbar (VSIX extension).

**Pipeline:**

1. Extension POSTs work item ID, organisation, project, and API key to the backend.
2. Validate the API key before doing anything else.
3. Fetch the current ADO revision number. Compute the document ID: `ado::{work_item_id}`. Query Qdrant for this ID and compare the stored revision. If unchanged, return early.
4. Fetch the full work item: all fields, acceptance criteria, last five comments.
5. Serialise to a plain text block: ID, type, state, title, area path, tags, assignee, description, acceptance criteria, comments.
6. Embed the serialised text.
7. Stage 2 similarity check. Log near-duplicates from different IDs.
8. Upsert using the deterministic ID. Qdrant overwrites the existing entry.
9. Store citation metadata including work_item_id, type, state, tags, direct browser URL, revision as content hash marker.
10. Return success. Extension updates the KB status badge.

**Optional AI summary path:** User clicks "Summarise & Register." The serialised text is sent to the LLM first. The summary is embedded instead of the raw text. `is_summary` is set to true. Everything else is the same.

---

### Teams Thread Ingestion _(post-MVP)_

**Trigger:** User right-clicks a message in a Teams channel thread and selects "Register in KB" from the `...` context menu (message action).

**Pipeline:**

1. Teams delivers a message action invoke to the backend with the message ID, channel ID, and team ID.
2. Validate the bot auth token.
3. Compute document ID: `teams::{thread_id}`. Query Qdrant for this ID. Compare stored message count and last-reply timestamp. If unchanged, return early.
4. Fetch all messages in the thread from Microsoft Graph — root message and all replies, ordered by time.
5. Filter to `messageType == "message"` only.
6. Run each message body through html2text. Discard messages under 10 words.
7. Flatten to a single block: date, sender, cleaned text per message.
8. Count tokens with tiktoken. If above ~12,000 tokens, trim from the oldest messages.
9. Send to the LLM for summarisation: core topic, decisions, action items, technical details.
10. If the LLM call fails, fall back to storing the cleaned raw thread text with `is_summary: false`. Do not drop the document.
11. Embed the summary (or fallback text).
12. Stage 2 similarity check. Log near-duplicates from different IDs.
13. Upsert using the deterministic ID.
14. Reply in the thread confirming registration.

---

## Query & Answer Architecture

Both modes use the same retrieval pipeline. They branch only at the response layer.

### Shared Retrieval Pipeline

1. Embed the question using bge-base-en-v1.5.
2. Search Qdrant for the top 20 most similar documents. Apply metadata filters if implied by the query (e.g. filter by `source_type` or `work_item_state`).
3. **Confidence threshold check:** if the top result's similarity score is below 0.65, return early with a "No relevant information found" response. Do not pass low-confidence results to the LLM — this is how hallucinations happen.
4. _(Post-MVP)_ Cross-encoder reranker rescores the top 20 using `source_priority` as a tiebreaker. For MVP, take the top 5 directly from Qdrant.

### Search Mode — `/query/search`

Returns the retrieval results directly. No LLM call.

Response per result:

- Title
- Source type
- Short snippet (first 300 characters of the stored content)
- Similarity score
- Direct link (source URI)

In Teams, triggered by the `/search <query>` command. This is useful for finding specific documents, debugging retrieval quality, and when users want to navigate to the source themselves rather than read a synthesised answer.

### Q&A Mode — `/query/answer`

Takes the top 5 retrieval results and passes them to the LLM.

The context block passed to the LLM includes for each document: title, source type, author, date, source URI, and the full stored content.

The LLM is instructed to answer only from the provided context, to say so clearly if the answer is not in the context, and to cite sources by title and source type.

The response includes the LLM-generated answer plus the source URIs appended as navigable links.

In Teams, this is the default mode when a user sends any message to the bot. The bot also posts a short "Sources used:" list below the answer.

---

## MVP Game Plan

### Week 1 — Infrastructure

- Provision the VM. Mount data disk. Install Docker.
- Run Qdrant as a Docker container on an internal port only.
- Get Nginx running with a valid HTTPS certificate. Set request size limit and basic rate limiting in Nginx config.
- Create the Qdrant collection with dense vector size 768. Create all payload indexes.

### Week 2 — PDF and Markdown Ingestion

- Build the batch script that walks the repo folder.
- Integrate PyMuPDF for PDF extraction and markdown-it-py + LangChain splitters for Markdown.
- Build the embedding service with bge-base-en-v1.5.
- Build the Qdrant upsert function with deterministic IDs and the full metadata schema (citation, embedding version, source priority).
- Implement the SHA-256 content hash check. Skip unchanged files.
- Implement the Stage 2 similarity check — log near-duplicates, no auto-delete.
- Run the script against a real sample. Confirm documents land in Qdrant with correct metadata.

### Week 3 — Search and Q&A Endpoints + ADO Ingestion

- Build `/query/search`: embed question, search Qdrant, apply confidence threshold, return raw results.
- Build `/query/answer`: take retrieval results, pass to LLM, return cited answer.
- Test search mode against ingested PDFs and Markdown. Verify retrieval quality directly — fix any chunking or metadata issues you find before adding the LLM layer.
- Create the ADO PAT. Build the ADO client and serialisation step.
- Wire up `/ingest/ado` with dedup and upsert.
- Build the VSIX extension with the toolbar button. Upload to private marketplace. Test against a real work item.

### Week 4 — Teams Bot

- Create the Azure App Registration. Get admin consent for Graph permissions.
- Create Azure Bot Service and Teams App. Sideload to a test team.
- Build the bot handler: messages default to Q&A mode, `/search <query>` triggers search mode.
- End-to-end test: ingest a PDF, a Markdown file, and a work item, then ask questions covering all three. Verify grounded cited answers with working source links.
- MVP complete.

### Post-MVP — Iteration Order

1. Teams thread ingestion: Graph fetch, html2text, AI summarisation, dedup, upsert.
2. Message action in Teams: wire the right-click menu action to the thread ingest endpoint.
3. Cross-encoder reranker between Qdrant search and LLM call, using source_priority as tiebreaker.
4. Hybrid BM25 sparse search alongside dense search in Qdrant.
5. Optional AI summary mode for ADO items.
6. ADO Service Hook for automatic ingestion on state change.
7. Async ingestion queue (Celery or RQ) for Teams summarisation and large PDF batches.
8. Re-embedding migration script: filter by `embedding_version`, re-embed, upsert with new version number.
