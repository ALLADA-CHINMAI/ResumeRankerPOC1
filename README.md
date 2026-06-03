# Resume Ranker

**AI-powered candidate screening for healthcare and enterprise recruitment**

Resume Ranker is an intelligent resume evaluation system that leverages Azure OpenAI GPT-4o, Azure Cognitive Search, and semantic embeddings to automatically rank candidate resumes against job descriptions. Built for scale and accuracy, it provides detailed scoring breakdowns across six evaluation categories, helping recruiters identify the best candidates quickly.

---

## 🌟 Capabilities

### Core Features
- **Intelligent Upload & Indexing** — Upload resumes and job descriptions (.txt, .pdf, .docx) via an intuitive Streamlit UI; documents are automatically extracted, chunked, embedded, and indexed in real-time
- **Hybrid Search** — Combines BM25 keyword search with vector similarity search for maximum relevance
- **Two-Stage Ranking Pipeline** — Stage 1 uses fast hybrid search to filter candidates; Stage 2 applies GPT-4o for deep semantic evaluation of only the top candidates (cost-optimized)
- **Multi-Category Scoring** — Each resume receives a total score (0–100) with granular breakdowns:
  - **Experience** (25 points) — Years of relevant work history
  - **Technical Skills** (30 points) — Proficiency in required technologies
  - **Certifications** (15 points) — Relevant professional certifications
  - **Education** (10 points) — Academic credentials
  - **Location** (10 points) — Geographic fit or remote readiness
  - **Domain Fit** (10 points) — Industry-specific experience alignment
- **Batch Processing** — Rank multiple resumes against a single job description in one operation
- **Explainable AI** — Each score includes detailed reasoning tied to specific resume content
- **Session Caching** — Resumes and embeddings are cached for fast re-ranking with different job descriptions

### Technical Capabilities
- **Modular Architecture** — Clean separation between business logic (`ResumeRankerCore`) and presentation layer (`ResumeRankerFrontend`)
- **MCP Server Integration** — Model Context Protocol server exposes three tools for GitHub Copilot Chat:
  - `search_resumes` — Hybrid semantic search across indexed resumes
  - `get_candidate_history` — Retrieve full interview history from Excel datastore
  - `rerank_candidates` — Azure OpenAI reranking with historical context
- **Candidate History Tracking** — Excel-based datastore (`candidate_data.xlsx`) maintains candidate metadata and complete interview round history
- **Enterprise Auth Support** — Azure AD token-based authentication with automatic token refresh for API Management/gateway scenarios
- **Auto-Scaling** — Parallelized GPT-4o scoring calls via ThreadPoolExecutor; configurable concurrency
- **Chunking Strategy** — Token-aware text splitting (400 tokens/chunk, 60-token overlap) preserves context across chunk boundaries
- **Embedding Optimization** — Batch embedding API calls (100 docs/batch) minimize latency
- **Zero Manual Configuration** — Search index auto-creates on first run; no Azure Portal setup required

---

## 🏗️ Architecture

### System Overview

```
┌───────────────────────────────────────────────────────────────────────┐
│                         Streamlit Web UI                              │
│                    (ResumeRankerFrontend/app.py)                     │
│   • Upload accordions (JDs & Resumes)                                │
│   • Selection dropdowns (JD + multi-select for resumes)              │
│   • Ranking button & results display with score breakdowns           │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────────────┐
│                       ResumeRankerCore                                │
│                    (Business Logic Layer)                             │
│                                                                       │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────────┐│
│  │  clients.py │  │  storage.py  │  │  search.py  │  │ ranking.py ││
│  │             │  │              │  │             │  │            ││
│  │ • OpenAI    │  │ • Blob CRUD  │  │ • Index mgmt│  │ • 2-stage  ││
│  │ • BlobSvc   │  │ • List/fetch │  │ • Chunk+emb │  │   pipeline ││
│  │ • Auth      │  │ • Parsed text│  │ • Hybrid    │  │ • GPT-4o   ││
│  │   refresh   │  │   caching    │  │   search    │  │   scoring  ││
│  └─────────────┘  └──────────────┘  └─────────────┘  └────────────┘│
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │              text_utils.py                                      │ │
│  │  • Text extraction (txt/pdf/docx)                               │ │
│  │  • Token-aware chunking with overlap                            │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────────────┐
│                        Azure Services                                 │
│                                                                       │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │  Azure OpenAI   │  │  Azure Cognitive │  │  Azure Blob        │  │
│  │                 │  │      Search      │  │    Storage         │  │
│  │ • GPT-4o        │  │                  │  │                    │  │
│  │ • text-embed-   │  │ • resume_chunks  │  │ • resumes/         │  │
│  │   ada-002       │  │   (vector index) │  │ • jds/             │  │
│  │ • Token refresh │  │ • Hybrid queries │  │ • resumes-parsed/  │  │
│  └─────────────────┘  └──────────────────┘  └────────────────────┘  │
└───────────────────────────────────────────────────────────────────────┘
```

### Component Details

#### 1. **ResumeRankerFrontend** (Presentation Layer)
- **app.py** — Streamlit UI with two main accordions:
  - **Upload** — Dual-column interface for uploading JDs and resumes with progress indicators
  - **Rank** — JD selector, resume multi-select dropdown, and rank button
  - Results display with top-N table and expandable per-candidate score breakdowns
- **Features:**
  - Session caching for blob listings (30s TTL) to minimize Azure API roundtrips
  - Real-time progress updates during ranking via callback functions
  - Responsive 100% width layout with minimal margins
  - Custom Providence Health branding (blue/olive color scheme)

#### 2. **ResumeRankerCore** (Business Logic Layer)

**clients.py** — Singleton clients for Azure services
- Lazy-initialized OpenAI client with automatic Azure AD token refresh
- BlobServiceClient for document storage
- Environment validation on startup
- Thread-safe token management (refreshes 5 minutes before expiry)

**storage.py** — Blob storage operations
- Upload/download documents to `resumes` and `jds` containers
- Manages `resumes-parsed` container for caching extracted plaintext
- Container auto-creation (no manual setup required)
- Batch operations for listing documents

**search.py** — Azure Cognitive Search client (`DocumentSearchClient`)
- Auto-creates vector search index with HNSW algorithm on first use
- Chunks documents into 400-token segments with 60-token overlap
- Batch embedding (100 docs/call) for performance
- Hybrid search (BM25 + vector) with configurable top-K and alpha blending
- Filters search by document name for targeted retrieval

**ranking.py** — Two-stage ranking pipeline
- **Stage 1 (Free):** Hybrid search aggregates chunk scores by resume → top 25 candidates
- **Stage 2 (Paid):** GPT-4o scores top 25 in parallel (ThreadPoolExecutor) with JSON schema validation
- Strict scoring rubric with gap analysis and hard-floor rules
- Reason generation for each category score
- Total score always equals sum of category scores (enforced via prompt)

**text_utils.py** — Document processing utilities
- Extracts text from .txt, .pdf, .docx using `pypdf`, `python-docx`
- Token-aware chunking using tiktoken (cl100k_base encoding)
- Recursive splitting on separators (paragraphs → sentences → hard token splits)
- LRU-cached encoder for performance

---

### Model Context Protocol (MCP) Server

**ResumeRankerMCP** exposes the resume ranking system as an MCP server for integration with GitHub Copilot Chat and other AI assistants.

#### Available Tools

**1. `search_resumes(query: str, top_k: int = 50) -> dict`**
- Performs hybrid semantic + keyword search across indexed resumes
- Returns candidates with `doc_name` and `semantic_score`
- Use `doc_name` values to fetch full candidate history

**2. `get_candidate_history(doc_names: List[str] | candidate_ids: List[str]) -> dict`**
- Reads `candidate_data.xlsx` (Excel datastore) for complete candidate profiles
- Returns metadata (name, email, role, years of experience, current company)
- Returns full interview history across all application rounds:
  - Interview dates, rounds cleared, scores
  - Rejection reasons and feedback
  - Application status and lifecycle state
- Resolves `doc_name` (from AI Search) → `candidate_id` → history rows

**3. `rerank_candidates(jd: str, candidates: List[Dict]) -> dict`**
- Accepts merged output from `search_resumes` + `get_candidate_history`
- Uses Azure OpenAI (GPT-4o) to produce final ranked list
- Considers semantic match, experience fit, interview history, and recency
- Returns ranked candidates with scores (0–100) and explanations

#### Excel Datastore Schema

**File:** `candidate_data.xlsx`

**Sheet 1: `candidate_metadata`**
| Column | Description |
|--------|-------------|
| `candidate_id` | Unique identifier |
| `full_name` | Candidate full name |
| `primary_email` | Contact email |
| `role_family` | Job role category |
| `years_experience` | Total YOE |
| `current_company` | Current employer |
| `latest_application_status` | Active/Rejected/Withdrawn |
| `last_interview_round` | Farthest round reached |
| `total_applications` | Count of applications |
| `candidate_status` | Overall lifecycle state |
| `latest_resume_blob_url` | Link to resume in blob storage |
| `latest_ai_search_doc_id` | Current resume doc_name in search index |

**Sheet 2: `interview_history`**
| Column | Description |
|--------|-------------|
| `candidate_id` | Reference to candidate_metadata |
| `application_date` | Date of application |
| `job_req_id` | Job requisition ID |
| `position_title` | Applied position |
| `interview_round` | Round number/name |
| `interview_date` | Round completion date |
| `interviewer_name` | Interviewer(s) |
| `technical_score` | Score for technical assessment |
| `behavioral_score` | Score for behavioral fit |
| `overall_feedback` | Text feedback |
| `rejection_reason` | If rejected, the reason |
| `round_status` | Passed/Failed/Pending |

#### VS Code Configuration

Add to `.vscode/settings.json`:

```json
{
  "github.copilot.chat.mcp.servers": {
    "resume-ranker": {
      "command": "python",
      "args": ["-m", "ResumeRankerMCP.server"],
      "cwd": "${workspaceFolder}",
      "env": {
        "PYTHONPATH": "${workspaceFolder}"
      }
    }
  }
}
```

Reload VS Code, then use in Copilot Chat:
```
@resume-ranker search for python developers with 5 years experience
```

#### Testing MCP Server

**Option 1: MCP Inspector (Recommended)**
```powershell
npm install -g @modelcontextprotocol/inspector
npx @modelcontextprotocol/inspector python -m ResumeRankerMCP.server
```

**Option 2: Direct Python Test**
```python
from ResumeRankerMCP.server import search_resumes, get_candidate_history

# Test search
results = search_resumes("senior python developer", top_k=10)
doc_names = [c["doc_name"] for c in results["candidates"][:5]]

# Test history lookup
history = get_candidate_history(doc_names=doc_names)
print(history)
```

**Option 3: Run as MCP Server**
```powershell
python -m ResumeRankerMCP.server
```

---

### Component Details

#### 1. **ResumeRankerFrontend** (Presentation Layer)
- **app.py** — Streamlit UI with two main accordions:
  - **Upload** — Dual-column interface for uploading JDs and resumes with progress indicators
  - **Rank** — JD selector, resume multi-select dropdown, and rank button
  - Results display with top-N table and expandable per-candidate score breakdowns
- **Features:**
  - Session caching for blob listings (30s TTL) to minimize Azure API roundtrips
  - Real-time progress updates during ranking via callback functions
  - Responsive 100% width layout with minimal margins
  - Custom Providence Health branding (blue/olive color scheme)

#### 2. **ResumeRankerCore** (Business Logic Layer)

**clients.py** — Singleton clients for Azure services
- Lazy-initialized OpenAI client with automatic Azure AD token refresh
- BlobServiceClient for document storage
- Environment validation on startup
- Thread-safe token management (refreshes 5 minutes before expiry)

**storage.py** — Blob storage operations
- Upload/download documents to `resumes` and `jds` containers
- Manages `resumes-parsed` container for caching extracted plaintext
- Container auto-creation (no manual setup required)
- Batch operations for listing documents

**search.py** — Azure Cognitive Search client (`DocumentSearchClient`)
- Auto-creates vector search index with HNSW algorithm on first use
- Chunks documents into 400-token segments with 60-token overlap
- Batch embedding (100 docs/call) for performance
- Hybrid search (BM25 + vector) with configurable top-K and alpha blending
- Filters search by document name for targeted retrieval

**ranking.py** — Two-stage ranking pipeline
- **Stage 1 (Free):** Hybrid search aggregates chunk scores by resume → top 25 candidates
- **Stage 2 (Paid):** GPT-4o scores top 25 in parallel (ThreadPoolExecutor) with JSON schema validation
- Strict scoring rubric with gap analysis and hard-floor rules
- Reason generation for each category score
- Total score always equals sum of category scores (enforced via prompt)

**text_utils.py** — Document processing utilities
- Extracts text from .txt, .pdf, .docx using `pypdf`, `python-docx`
- Token-aware chunking using tiktoken (cl100k_base encoding)
- Recursive splitting on separators (paragraphs → sentences → hard token splits)
- LRU-cached encoder for performance

#### 3. **ResumeRankerMCP** (Model Context Protocol Server)

**server.py** — FastMCP server exposing three tools
- `@mcp.tool()` decorator for tool registration
- Integrates with ResumeRankerCore clients (OpenAI, Azure Search)
- JSON response formatting for LLM consumption
- Error handling with fallback responses

**candidate_history.py** — Excel datastore integration
- Reads `candidate_data.xlsx` with two sheets (metadata + history)
- `get_by_doc_names()` — Resolve AI Search doc_name → candidate records
- `get_by_candidate_ids()` — Direct candidate ID lookup
- Returns merged candidate profiles with full interview history arrays
- Uses pandas + openpyxl for Excel reading

**Configuration:**
- Runs via `python -m ResumeRankerMCP.server`
- Configurable via `CANDIDATE_DATA_PATH` env var (defaults to `../candidate_data.xlsx`)
- Integrates with VS Code GitHub Copilot Chat via `.vscode/settings.json`

#### 4. **Azure Services Integration**

**Azure OpenAI**
- **GPT-4o:** Used for semantic scoring (Stage 2) and JD keyword extraction
- **text-embedding-ada-002:** Generates 1536-dimensional embeddings for vector search
- Supports both direct Azure OpenAI endpoints and APIM/gateway scenarios with Azure AD auth

**Azure Cognitive Search**
- **Index Schema:**
  - `chunk_id` (unique key: SHA-256 hash of content)
  - `resume_name` or `jd_name` (document identifier)
  - `chunk_text` (searchable text, BM25)
  - `chunk_embedding` (vector field, HNSW, 1536 dims)
- **Query Strategy:** Hybrid search with alpha=0.5 (equal weight to BM25 and vector similarity)

**Azure Blob Storage**
- **resumes/** — Raw resume files as uploaded
- **jds/** — Raw job description files as uploaded
- **resumes-parsed/** — Extracted plaintext cache (generated on first upload)

---

## 🔄 Detailed Workflow

### 1. Resume Upload & Indexing

```
User selects resumes → Upload button clicked
    ↓
For each file:
    ↓
    1. Extract text (text_utils.extract_text)
       • .txt → direct read
       • .pdf → pypdf page-by-page extraction
       • .docx → python-docx paragraph extraction
    ↓
    2. Upload raw file to Azure Blob (storage.upload_blob)
       • Container: resumes/
       • Filename: original name
    ↓
    3. Cache plaintext (storage.store_parsed_text)
       • Container: resumes-parsed/
       • Used for Stage 2 GPT-4o scoring
    ↓
    4. Chunk text (text_utils.chunk_text)
       • 400 tokens per chunk
       • 60-token overlap between chunks
       • Recursive splitting: \n\n → \n → . → hard token split
    ↓
    5. Embed chunks (search.DocumentSearchClient.index_document)
       • Batch API call to text-embedding-ada-002 (100 chunks/call)
       • Generate 1536-dim vectors
    ↓
    6. Upload to Azure Cognitive Search
       • chunk_id = SHA-256(resume_name + chunk_text)
       • Upsert to resume_chunks index
    ↓
    Progress indicator updates in UI
    ↓
Success message: "Uploaded & indexed N resumes"
```

### 2. Job Description Upload

```
User selects JD file → Upload button clicked
    ↓
    1. Extract text (text_utils.extract_text)
    ↓
    2. Upload raw file to Azure Blob
       • Container: jds/
    ↓
    3. Optional: Index JD chunks (search.DocumentSearchClient.index_document)
       • Same chunking/embedding process as resumes
       • Index: jd_chunks (separate from resumes)
    ↓
Success message: "Uploaded & indexed JD"
```

### 3. Resume Ranking (Two-Stage Pipeline)

#### **User Action:**
```
User selects JD from dropdown
User selects resumes from multi-select (or keeps default: all resumes)
User clicks "Rank Resumes →" button
```

#### **Stage 1: Hybrid Search (Free, Fast)**

```
ranking.rank_resumes() called
    ↓
    1. Fetch JD full text from blob storage
    ↓
    2. FOR selected resume filtering (optional):
       • If user selected subset → filter search by those names
       • Else → search all resumes
    ↓
    3. Extract JD keywords (GPT-4o query_text prompt)
       • Input: Full JD text
       • Output: Dense 2–3 sentence summary of must-have skills/requirements
    ↓
    4. Hybrid search (search.DocumentSearchClient.search)
      • Query: LLM-generated summary of must-have JD requirements (from GPT-4o prompt)
       • BM25 (keyword matching) + vector similarity
       • Alpha = 0.5 (equal weight)
       • Top-K = 60 chunks (configurable)
       • Filter: resume_name in selected_resumes (if specified)
    ↓
    5. Aggregate scores by resume
       • Group 60 chunks by resume_name
       • Sum search scores per resume
    ↓
    6. Rank by aggregate score → Keep top 25 resumes
       • Only top 25 proceed to Stage 2 (GPT-4o scoring)
       • Cost optimization: Cap GPT-4o calls regardless of corpus size
    ↓
Progress: "Stage 1 complete: 25 candidates shortlisted"
```

#### **Stage 2: GPT-4o Semantic Scoring (Paid, Accurate)**

```
For each of top 25 resumes (in parallel):
    ↓
    1. Fetch full resume text from resumes-parsed/ cache
    ↓
    2. Call GPT-4o with structured prompt (ranking.SCORE_SYSTEM)
       • System: Strict rubric with gap analysis + hard-floor rules
       • User: Full JD text + full resume text
       • Response format: JSON with scores, reasons, totalScore
    ↓
    3. Parse JSON response
       • Validate: totalScore == sum(category scores)
       • Extract scores dict, reasons dict
    ↓
    4. Append to results list
    ↓
Progress: "Scored resume N of 25"
    ↓
All parallel calls complete via ThreadPoolExecutor.as_completed()
    ↓
Sort results by totalScore descending
    ↓
Return top 10 candidates
    ↓
Status: "Ranking complete!"
```

#### **Results Display:**

```
UI renders:
    ↓
    1. Summary message: "Top 10 candidates for [JD name]"
    ↓
    2. Table (Rank | Resume | Score)
       • Sortable dataframe (use_container_width=True)
    ↓
    3. Score Breakdown Expanders
       • For each candidate: expandable panel
       • 6 metric cards (Experience, Tech Skills, Certs, etc.)
       • Reasons listed under "Scoring Reasons:"
```

### Scoring Rubric Details

**Gap Analysis (Step 1 in GPT-4o prompt):**
- Extract 6–8 key JD requirements
- Find exact resume evidence for each requirement
- Count: fully_met / partially_met / not_found

**Hard-Floor Rules (enforced before scoring):**
- Resume years < JD required → Experience ≤ 18
- 2+ missing required skills → Technical Skills ≤ 18
- 3+ missing required skills → Technical Skills ≤ 12
- Required cert absent → Certifications ≤ 8
- Location mismatch (no remote mention) → Location ≤ 3

**Calibration Scale:**
- **90–100:** All requirements explicitly met (top 5% of applicants)
- **75–89:** Most requirements met, 1–2 minor gaps
- **55–74:** Core requirements met, notable gaps
- **35–54:** Several requirements unmet
- **0–34:** Largely unrelated background

---

## 🚀 Setup & Installation

### Prerequisites

- **Python 3.10 or higher**
- **Azure Subscription** with:
  - **Azure Blob Storage** account (Standard tier)
  - **Azure Cognitive Search** service (Basic or higher — Free tier does not support vector search)
  - **Azure OpenAI** resource with deployments:
    - `gpt-4o` (or gpt-4, gpt-35-turbo)
    - `text-embedding-ada-002`

### Installation (Windows — PowerShell)

#### 1. Clone the repository

```powershell
git clone <your-repo-url>
cd resumeRanking
```

#### 2. Create and activate virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> **Execution Policy Error?**  
> Run once: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

#### 3. Install dependencies

```powershell
pip install -r requirements.txt
```

**Key Dependencies:**
- `streamlit` — Web UI framework
- `azure-search-documents>=11.4.0` — Vector search support
- `openai` — Azure OpenAI client
- `tiktoken` — Token counting for chunking
- `pandas` + `openpyxl` — Excel datastore for candidate history
- `mcp[cli]` — Model Context Protocol server
- `langgraph` + `langchain` — Workflow orchestration
- `python-docx` + `pdfplumber` — Document parsing

#### 4. Set up candidate history datastore (optional)

If using MCP server with interview history tracking:

1. Create `candidate_data.xlsx` in project root
2. Add two sheets:
   - **Sheet 1:** `candidate_metadata` (columns: candidate_id, full_name, primary_email, role_family, years_experience, current_company, latest_application_status, last_interview_round, total_applications, candidate_status, latest_resume_blob_url, latest_ai_search_doc_id)
   - **Sheet 2:** `interview_history` (columns: candidate_id, application_date, job_req_id, position_title, interview_round, interview_date, interviewer_name, technical_score, behavioral_score, overall_feedback, rejection_reason, round_status)

See **Model Context Protocol (MCP) Server** section for full schema details.

> **Note:** MCP tools will return empty results if `candidate_data.xlsx` is not found. Search and ranking functionality works without it.

#### 5. Configure environment variables

```powershell
Copy-Item .env.example .env
```

Edit `.env` with your Azure credentials:

```env
# Azure OpenAI
OPENAI_ENDPOINT=https://your-resource.openai.azure.com
OPENAI_DEPLOYMENT_NAME=gpt-4o
OPENAI_API_KEY=your-openai-api-key
EMBEDINGS_OPENAI_DEPLOYMENT_NAME=text-embedding-ada-002
OPENAI_API_VERSION=2025-01-01-preview

# Azure Cognitive Search
AZURE_SEARCH_ENDPOINT=https://your-service.search.windows.net
AZURE_SEARCH_API_KEY=your-search-admin-key
AZURE_SEARCH_RESUME_INDEX_NAME=resume_chunks
AZURE_SEARCH_JD_INDEX_NAME=jd_chunks

# Azure Blob Storage
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
RESUME_CONTAINER_NAME=resumes
JD_CONTAINER_NAME=jds

# Optional: Candidate history datastore (for MCP server)
# CANDIDATE_DATA_PATH=candidate_data.xlsx

# Optional: Azure AD Auth (for APIM/gateway scenarios)
# AUTH_TENANT_ID=<your-tenant-id>
# AUTH_CLIENT_ID=<your-client-id>
# AUTH_CLIENT_SECRET=<your-client-secret>
# AUTH_SCOPE=https://cognitiveservices.azure.com/.default
```

**Where to Find Values:**

| Variable | Azure Portal Location |
|----------|----------------------|
| `OPENAI_ENDPOINT` | Azure OpenAI → Overview → Endpoint |
| `OPENAI_API_KEY` | Azure OpenAI → Keys and Endpoint → KEY 1 |
| `AZURE_SEARCH_ENDPOINT` | AI Search → Overview → Url |
| `AZURE_SEARCH_API_KEY` | AI Search → Keys → Primary admin key |
| `AZURE_STORAGE_CONNECTION_STRING` | Storage Account → Access keys → Connection string |

#### 6. Create blob containers

**Option A: Azure Portal**
- Navigate to Storage Account → Containers → + Container
- Create two containers (Access level: Private):
  - `resumes`
  - `jds`

**Option B: Azure CLI**

```powershell
az storage container create --name resumes --connection-string $env:AZURE_STORAGE_CONNECTION_STRING
az storage container create --name jds --connection-string $env:AZURE_STORAGE_CONNECTION_STRING
```

> The `resumes-parsed` container is auto-created by the app.

#### 7. Run the application

**Streamlit UI:**
```powershell
cd ResumeRankerFrontend
streamlit run app.py
```

The app opens at **http://localhost:8501**

**MCP Server (for GitHub Copilot Chat):**
1. Ensure `.vscode/settings.json` contains MCP server configuration (see MCP section above)
2. Reload VS Code window (`Ctrl+Shift+P` → "Developer: Reload Window")
3. Use `@resume-ranker` in GitHub Copilot Chat

The Azure Cognitive Search index (`resume_chunks`) is **auto-created on first run** — no manual setup required.

---

## 📖 Usage Guide

### Upload Resumes

1. Expand **"📤 Upload New Job Descriptions & Resumes"** accordion
2. In the **right column** (Upload New Resumes):
   - Click **Browse files**
   - Select one or more files (.txt, .pdf, .docx)
   - Click **Upload & Index Resumes**
3. Watch the progress bar as each resume is processed
4. Success message shows count of indexed resumes

### Upload Job Descriptions

1. In the same accordion, **left column** (Upload New Job Descriptions):
   - Click **Browse files**
   - Select JD file (.txt, .pdf, .docx)
   - Click **Upload JDs**
2. Success message confirms upload

### Rank Resumes

1. Expand **"📊 Rank Resumes Against a Job Description"** accordion
2. **Select Job Description** (left column):
   - Choose a JD from the dropdown
3. **Select Resumes to Rank** (right column):
   - Multi-select dropdown shows all indexed resumes
   - Default: All resumes selected
   - Remove any resumes you want to exclude
4. Click **"Rank Resumes →"** button (below accordions)
5. View results:
   - **Top N Candidates** table (Rank | Resume | Score)
   - **Score Breakdown** — Expand any candidate to see:
     - 6 metric cards (Experience, Tech Skills, Certs, Education, Location, Domain Fit)
     - Detailed reasoning for each category score

---

## 📁 Project Structure

```
resumeRanking/
├── ResumeRankerCore/           # Business logic layer (no Streamlit deps)
│   ├── __init__.py
│   ├── clients.py              # Azure OpenAI, Blob, Auth clients
│   ├── storage.py              # Blob CRUD operations
│   ├── search.py               # DocumentSearchClient (hybrid search)
│   ├── ranking.py              # Two-stage ranking pipeline
│   ├── text_utils.py           # Text extraction & chunking
│   └── langgraph_pipeline.py   # LangGraph workflow orchestration
│
├── ResumeRankerFrontend/       # Presentation layer
│   └── app.py                  # Streamlit UI (accordions, ranking, results)
│
├── ResumeRankerMCP/            # Model Context Protocol server
│   ├── __init__.py
│   ├── server.py               # FastMCP server with 3 tools
│   └── candidate_history.py   # Excel datastore integration
│
├── ResumesAndJdSampleData/     # Sample data for testing
│   ├── actual-resumes/
│   ├── actual-servicenow-resumes/
│   └── jds/
│
├── .vscode/
│   └── settings.json           # MCP server configuration for Copilot Chat
│
├── candidate_data.xlsx         # Candidate metadata + interview history
├── .env.example                # Template for environment variables
├── .env                        # Your local config (git-ignored)
├── requirements.txt            # Python dependencies
└── README.md                   # This file
```

---

## 🛠️ Troubleshooting

### **Execution policy error when activating venv**
**Solution:** Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` (no admin needed)

### **`ModuleNotFoundError: No module named 'ResumeRankerCore'`**
**Cause:** Running from wrong directory  
**Solution:** Always run `streamlit run ResumeRankerFrontend\app.py` from the `resumeRanking` root directory

### **`Index creation fails` or `Vector search not supported`**
**Cause:** Free tier Azure Cognitive Search doesn't support vector fields  
**Solution:** Upgrade to Basic tier or higher

### **`Empty results after ranking`**
**Checklist:**
- ✅ Resume uploaded successfully (green success message)?
- ✅ Blob containers `resumes` and `jds` exist?
- ✅ `EMBEDINGS_OPENAI_DEPLOYMENT_NAME` in `.env` matches actual deployment name?
- ✅ Index `resume_chunks` exists in Azure Cognitive Search?

### **`401 Unauthorized` from Azure OpenAI**
**Scenario 1: Direct Azure OpenAI endpoint**
- Verify `OPENAI_API_KEY` is correct (copy from Azure Portal → Keys and Endpoint)

**Scenario 2: APIM/gateway with Azure AD**
- Set all auth variables: `AUTH_TENANT_ID`, `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`, `AUTH_SCOPE`
- Keep `OPENAI_API_KEY` if gateway expects subscription key + bearer token
- Restart Streamlit after changing `.env` (cached clients must reinitialize)

### **`ResourceNotFoundError` on blob operations**
**Cause:** Container doesn't exist  
**Solution:** Create `resumes` and `jds` containers manually (see Setup Step 5)

### **Slow ranking performance**
**Optimizations:**
- Reduce `top_n` in `rank_resumes()` (default 10)
- Lower `max_candidates` in Stage 1 (default 25)
- Increase `max_workers` in ThreadPoolExecutor for more parallel GPT-4o calls (default 5)

### **Token limit exceeded errors**
**Cause:** Very long resumes or JDs exceed GPT-4o context window  
**Solutions:**
- Reduce `CHUNK_TOKENS` in `text_utils.py` (default 400)
- Truncate input text before scoring
- Use `gpt-4-32k` deployment for larger context window

### **MCP server not appearing in Copilot Chat**
**Checklist:**
- ✅ `.vscode/settings.json` contains `github.copilot.chat.mcp.servers` configuration
- ✅ VS Code window reloaded after adding MCP config (`Ctrl+Shift+P` → "Developer: Reload Window")
- ✅ Virtual environment activated with `mcp` package installed
- ✅ `PYTHONPATH` in MCP config points to workspace root

**Debug:** Check VS Code Output panel (View → Output → select "GitHub Copilot Chat" from dropdown) for MCP server startup errors

### **`FileNotFoundError: candidate_data.xlsx not found`**
**Cause:** MCP tool `get_candidate_history` called without Excel datastore  
**Solutions:**
- Create `candidate_data.xlsx` in project root (see Setup Step 4)
- Set `CANDIDATE_DATA_PATH` environment variable to custom location
- MCP `search_resumes` and core ranking still work without history data

### **`candidate_data.xlsx` returns empty results**
**Checklist:**
- ✅ Sheet names exactly match: `candidate_metadata` and `interview_history`
- ✅ `latest_ai_search_doc_id` column in metadata sheet matches `doc_name` from search results
- ✅ `candidate_id` column exists in both sheets (used for joining)
- ✅ No hidden rows/columns or filters applied in Excel

### **MCP tools return errors in Copilot Chat**
**Debug steps:**
1. Test tools directly in Python:
   ```python
   from ResumeRankerMCP.server import search_resumes
   print(search_resumes("python developer", top_k=5))
   ```
2. Run MCP server manually: `python -m ResumeRankerMCP.server`
3. Check terminal output for import errors or missing environment variables
4. Verify all Azure credentials in `.env` are current

---

## 🔒 Security & Compliance

- **Data Storage:** All documents stored in Azure Blob Storage with private access level
- **Candidate History:** Interview data stored locally in Excel (`candidate_data.xlsx`) — ensure this file is **excluded from version control** and secured with appropriate file system permissions
- **API Keys:** Managed via environment variables (never committed to git)
- **Azure AD Integration:** Supports token-based auth for enterprise environments
- **Token Auto-Refresh:** Prevents expired token errors in long-running sessions
- **MCP Server:** Runs locally; does not expose network endpoints (stdio transport only)
- **HIPAA/PII Considerations:** 
  - No PHI/PII is sent to OpenAI beyond what's in uploaded resumes/JDs
  - `candidate_data.xlsx` contains PII (names, emails) — follow organizational data governance policies
  - Azure Cognitive Search index contains resume content (PII) — ensure appropriate access controls
  - Review your organization's policies before use in production

**Recommended Security Practices:**
- Add `candidate_data.xlsx` to `.gitignore` (already included in standard Python `.gitignore`)
- Use Azure Storage encryption at rest and in transit
- Rotate API keys regularly (Azure OpenAI, Cognitive Search)
- Enable Azure AD authentication for production deployments
- Limit blob container access to least privilege (consider SAS tokens instead of connection strings)

---

## 📊 Performance Benchmarks

**Typical Performance (100 resumes, 1 JD):**
- **Stage 1 (Hybrid Search):** ~2 seconds (filters to top 25)
- **Stage 2 (GPT-4o Scoring):** ~8–12 seconds (25 resumes in parallel, 5 workers)
- **Total Ranking Time:** ~10–15 seconds
- **Cost per Ranking:** ~$0.10–0.15 (based on GPT-4o pricing, 25 resumes scored)

**Scaling:**
- Cost and Stage 2 time are **capped** because only top 25 candidates proceed to GPT-4o
- With 1,000 resumes, ranking time remains ~10–15 seconds (Stage 1 scales logarithmically with vector search)

---

## 🔄 Candidate History Integration

When using the MCP server's `rerank_candidates` tool, the system considers historical interview data from `candidate_data.xlsx` to produce more informed rankings. This enhances traditional resume matching with institutional memory.

### Factors Considered During Reranking

| Factor | Description | Impact on Score |
|--------|-------------|-----------------|
| **Resume Upload Date** | Recency of candidate's application | Recent applications prioritized; candidates with resumes >6 months old may be filtered |
| **Has Past Interview History** | Whether candidate has been interviewed before | Returning candidates evaluated with additional context |
| **Applied Role** | Previous positions the candidate applied for | Indicates career trajectory and role consistency |
| **Interview Round Cleared** | Farthest round reached in past interviews | High-performing candidates from past rounds boosted |
| **Interview Scores** | Technical and behavioral scores from history | Strong past performance increases ranking |
| **Rejection Reason** | Why candidate was previously rejected | Red flags (e.g., culture fit issues) may lower score |
| **Domain Expertise** | Industry experience from history + resume | Healthcare/domain-specific experience weighted higher |
| **Skill Match** | Technical skills from resume vs. JD requirements | Core competency alignment from both resume and past interviews |
| **Experience Match** | Years of experience and role seniority | Under/over-qualification identified via history data |
| **Project Relevance** | Past work on similar initiatives | Project portfolios evaluated across applications |
| **Multiple Job Switches** | Frequency of job changes | Excessive switching (>3 jobs in 2 years) may indicate retention risk |

### Reranking Workflow with History

```
User triggers rerank_candidates via MCP tool
    ↓
1. search_resumes() returns semantic matches with doc_names
    ↓
2. get_candidate_history(doc_names) fetches Excel records
    ↓
3. Merged data passed to Azure OpenAI (GPT-4o)
   • Input: JD + resume text + full interview history
   • System prompt includes instructions to weight history factors
    ↓
4. LLM produces ranked list with explanations
   • Score: 0–100
   • Explanation: Cites resume content + interview history
   • Example: "Strong Python skills (resume) + passed 3 previous
              technical rounds (2024-01 interview) + rejected for
              culture fit (manageable gap) = 82/100"
    ↓
5. Return JSON with ranked candidates
```

### Benefits of History-Aware Ranking

- **Institutional Memory:** Avoid re-interviewing candidates rejected for fundamental fit issues
- **Fast-Track Qualified Returners:** Candidates who previously cleared technical rounds require less vetting
- **Career Trajectory Analysis:** Detect patterns in role progression and job stability
- **Bias Reduction:** Historical scores provide objective data points beyond resume keywords
- **Feedback Loop:** Rejection reasons inform future candidate evaluations 
