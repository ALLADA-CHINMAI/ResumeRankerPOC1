# ResumeRanker POC1 — Architecture & Data Flows

## Overview

ResumeRanker ingests resumes and job descriptions (JDs), ranks candidates against open roles using Azure AI, and exposes results through three interfaces: a Streamlit UI, a REST API, and an MCP server (for AI assistant integration).

The system is designed for SuccessFactors integration: files land in Azure Blob Storage (via UI or SuccessFactors push), Azure Functions process them automatically (extract + index + store), and ranking runs on-demand via the two-stage pipeline.

---

## Components

```
ResumeRankerPOC1/
├── ResumeRankerCommon/        # Shared business logic — no UI or framework imports
│   ├── clients.py             # Azure client singletons (OpenAI, Blob, Search)
│   ├── config_manager.py      # All DB operations (SQL Server via pyodbc)
│   ├── config.py              # Scoring config loader (scoring_config.json)
│   ├── models.py              # Pydantic models (RankedCandidate)
│   ├── ranking.py             # Two-stage ranking pipeline
│   ├── search.py              # Azure Cognitive Search client wrapper
│   ├── storage.py             # Blob storage helpers + DB-first fallback paths
│   └── text_utils.py          # PDF/DOCX text extraction + token-based chunking
│
├── ResumeRankerFrontend/      # Streamlit UI
│   └── app.py                 # Upload JDs/resumes → blob; display ranking results
│
├── ResumeRankerAPI/           # FastAPI REST endpoint
│   └── main.py                # POST /rankResumes — used by external systems
│
├── ResumeRankerMCP/           # Model Context Protocol server (AI assistant tool)
│   ├── server.py              # FastMCP server entry point
│   └── tools/                 # catalog.py, search.py, ranking.py, resume.py
│
├── ResumeRankerFunctions/     # Azure Functions — event-driven file processing
│   ├── function_app.py        # 2 BlobTrigger functions (resume + JD)
│   ├── host.json
│   └── requirements.txt
│
├── db_migrations/             # SQL Server scripts (run once in SSMS)
│   ├── 001_create_tables.sql
│   ├── 002_create_stored_procs.sql
│   └── 003_verify.sql
│
└── scripts/
    └── backfill_db.py         # One-time migration of existing blob/xlsx data → DB
```

---

## Database Schema

```
JD_Metadata
  id, req_id (SuccessFactors ID), name (filename), blob_url,
  full_text (extracted JD text), created_at, updated_at

Candidate_Profile
  id, resume_name (filename = Search index key), email, full_name, phone,
  linkedin, role_family, years_experience, current_company,
  resume_blob_url, parsed_text (full resume text for Stage 2 scoring),
  ai_search_doc_id, created_at, updated_at

Interview_History
  id, candidate_id → Candidate_Profile, interview_date, round_type,
  round_number, result, job_role, req_id

Ranking_Cache
  id, jd_id → JD_Metadata, candidate_id → Candidate_Profile,
  total_score, scores (JSON), reasons (JSON), scored_at
  UNIQUE (jd_id, candidate_id)
  — table exists for future use; not populated by current pipeline
```

Upserts use stored procedures (`sp_upsert_jd`, `sp_upsert_candidate_file`, `sp_upsert_candidate_profile`, `sp_insert_interview_history`). All DB operations are in `config_manager.py`.

---

## Ranking Pipeline

### Stage 1 — Hybrid Search (free, milliseconds)
`resume_chunks` Azure Cognitive Search index stores chunked resume text with both BM25 (keyword) and vector (embedding) representations. Given a JD, `extract_jd_keywords()` distills it to dense keyword text, then `hybrid_search()` finds the top-K most relevant resume chunks. Multiple chunks per resume are aggregated by score, giving a coarse ranked list.

### Stage 2 — GPT-4o Scoring (paid, ~30–60s)
The top 15 candidates from Stage 1 are scored in parallel (up to 8 threads) by GPT-4o. The scoring prompt is configured in `scoring_config.json` and scores each candidate across 6 categories (experience, technicalSkills, certifications, education, location, domainFit) with a 0–100 total.

---

## Data Flows

### 1. Resume Upload (UI or SuccessFactors)

```
User/SuccessFactors → upload_blob("resumes/", bytes)
                        ↓
                   Azure Blob Storage
                        ↓  BlobTrigger fires automatically
process_new_resume():
  extract_text(name, data)               ← PDF/DOCX → plain text  (zero GPT)
  resume_search.index_document()         → resume_chunks index (chunked + embedded)
  upsert_candidate_file(parsed_text)     → Candidate_Profile row in DB
```

### 2. JD Upload (UI or SuccessFactors)

```
User/SuccessFactors → upload_blob("jds/", bytes, metadata={req_id})
                        ↓
                   Azure Blob Storage
                        ↓  BlobTrigger fires automatically
process_new_jd():
  extract_text(name, data)                        (zero GPT)
  upsert_jd(full_text, req_id, blob_url)  → JD_Metadata row in DB
  (No Search indexing — DB full_text replaces jd_chunks)
```

### 3. Ranking (UI click / API call)

```
UI "Rank Resumes" click:
  get_jd_full_text(selected_jd)   → JD_Metadata.full_text (DB, fast, no blob fetch)
  rank_resumes(jd_text, top_n=10)
    Stage 1: extract_jd_keywords() → dense keyword string (GPT, cached)
             hybrid_search()       → top 15 resume names from resume_chunks
    Stage 2: parallel score_resume() → GPT-4o scores each of top 15 (~30–60s)
  display ranked results

API POST /rankResumes:
  find_jd_by_req_id(req_id)   → JD_Metadata (single DB query)
  rank_resumes(jd_text, jd_name=jd_name)  → same pipeline
```

---

## Key Design Decisions

| Decision | Reason |
|----------|--------|
| All extraction/indexing in BlobTrigger Functions only | UI and SuccessFactors use identical upload path — zero code change when SuccessFactors goes live |
| Zero GPT in BlobTriggers | GPT at upload time = per-file cost at ingest scale. GPT runs only at ranking time when a user is waiting for results |
| DB replaces jd_chunks Search index | `jd_chunks` was only used for req_id lookup and text reconstruction. `JD_Metadata.full_text` is simpler, cheaper, and directly queryable |
| DB replaces resumes-parsed blob container | `Candidate_Profile.parsed_text` serves the same purpose with better queryability and no extra blob container |
| DB-first reads everywhere | UI, API, and MCP all read JD text and resume text from DB first; blob is fallback only for pre-migration data |

---

## Environment Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `OPENAI_ENDPOINT` | All | Azure OpenAI endpoint URL |
| `OPENAI_API_KEY` | All | Azure OpenAI API key |
| `OPENAI_API_VERSION` | All | API version (e.g. `2025-01-01-preview`) |
| `OPENAI_DEPLOYMENT_NAME` | ranking | GPT-4o deployment name |
| `EMBEDDINGS_OPENAI_DEPLOYMENT_NAME` | search | Embedding model deployment name |
| `AZURE_SEARCH_ENDPOINT` | search | Cognitive Search endpoint |
| `AZURE_SEARCH_API_KEY` | search | Cognitive Search API key |
| `AZURE_SEARCH_RESUME_INDEX_NAME` | search | Resume chunks index (default: `resume_chunks`) |
| `AZURE_STORAGE_CONNECTION_STRING` | blob, functions | Storage account connection string |
| `RESUME_CONTAINER_NAME` | blob | Container for resume files (default: `resumes`) |
| `JD_CONTAINER_NAME` | blob | Container for JD files (default: `jds`) |
| `DB_CONNECTION_STRING` | config_manager | SQL Server ODBC connection string |

---

## Setup & Deployment

### 1. Database (run once in SSMS)
```sql
-- Run in order against your Azure SQL / SQL Server instance:
db_migrations/001_create_tables.sql       -- creates 4 tables
db_migrations/002_create_stored_procs.sql -- creates 4 stored procs
```

### 2. Python environment
```powershell
# Root environment (UI + API + MCP)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Functions environment (separate venv)
cd ResumeRankerFunctions
python -m venv .venv-func
.venv-func\Scripts\activate
pip install -r requirements.txt
```

### 3. Configuration
```powershell
copy .env.example .env   # fill in real values
# fill in ResumeRankerFunctions\local.settings.json
```

### 4. Backfill existing data (one time)
```powershell
python scripts/backfill_db.py --all
```

### 5. Run locally
```powershell
# Streamlit UI
streamlit run ResumeRankerFrontend\app.py

# REST API
uvicorn ResumeRankerAPI.main:app --host 0.0.0.0 --port 8000

# MCP server
python -m ResumeRankerMCP.server

# Azure Functions (requires Azure Functions Core Tools)
cd ResumeRankerFunctions && func start
```

---

## What Was Retired

| Component | Replaced By |
|-----------|------------|
| `jd_chunks` Azure Search index | `JD_Metadata.full_text` in SQL DB |
| `resumes-parsed` blob container | `Candidate_Profile.parsed_text` in SQL DB |
| `candidate_data.xlsx` | `Candidate_Profile` + `Interview_History` tables |
| Inline UI processing (extract + index on upload click) | BlobTrigger Functions fire automatically |
| `get_jd_search()` in `clients.py` | Removed — JD Search index retired |
