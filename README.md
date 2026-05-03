# Resume Ranker

AI-powered resume screening. Upload resumes and job descriptions via a Streamlit UI — the app ranks all resumes against a selected JD and returns the top 10 candidates with a per-category score breakdown.

---

## How it works

1. **Upload resumes** — the app extracts text, splits into chunks, embeds each chunk via `text-embedding-ada-002`, and pushes to Azure Cognitive Search (no indexer skillset needed)
2. **Upload a JD** — stored as-is in Azure Blob Storage
3. **Rank** — select a JD, click Rank Resumes:
   - GPT-4o extracts dense keywords from the JD
   - Hybrid search (BM25 + vector) retrieves the top 60 most relevant chunks
   - Chunks are grouped by resume filename
   - GPT-4o scores each unique resume (0–100) against the full JD
   - Top 10 are returned with an expandable score breakdown

**Score categories:** Experience (25) · Technical Skills (30) · Certifications (15) · Education (10) · Location (10) · Domain Fit (10)

---

## Prerequisites

- Python 3.10 or higher
- An **Azure Blob Storage** account with two containers: `resumes` and `jds`
- An **Azure Cognitive Search** service (Basic tier or higher — Free tier does not support vector search)
- An **Azure OpenAI** resource with two deployments:
  - `gpt-4o` (or your deployment name)
  - `text-embedding-ada-002` (or your embedding deployment name)

---

## Setup (Windows — PowerShell)

### 1. Clone the repo

```powershell
git clone <your-repo-url>
cd ResumeRankerPOC1
```

### 2. Create and activate a virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> If you get an execution policy error, run this once:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### 3. Install dependencies

```powershell
pip install -r requirements.txt
```

### 4. Configure environment variables

```powershell
Copy-Item .env.example .env
```

Open `.env` in any editor and fill in all values:

```
OPENAI_ENDPOINT=https://your-resource.openai.azure.com
OPENAI_DEPLOYMENT_NAME=gpt-4o
OPENAI_API_KEY=your-openai-api-key
EMBEDINGS_OPENAI_DEPLOYMENT_NAME=text-embedding-ada-002
OPENAI_API_VERSION=2025-01-01-preview

AZURE_SEARCH_ENDPOINT=https://your-service.search.windows.net
AZURE_SEARCH_API_KEY=your-search-admin-key
AZURE_SEARCH_RESUME_INDEX_NAME=resume_chunks

AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net
RESUME_CONTAINER_NAME=resumes
JD_CONTAINER_NAME=jds
```

Where to find each value:

| Variable | Azure Portal location |
|---|---|
| `OPENAI_ENDPOINT` | OpenAI resource → Overview → Endpoint |
| `OPENAI_API_KEY` | OpenAI resource → Keys and Endpoint → KEY 1 |
| `OPENAI_API_VERSION` | Usually keep the default unless your gateway requires a different preview version |
| `AZURE_SEARCH_ENDPOINT` | AI Search service → Overview → Url |
| `AZURE_SEARCH_API_KEY` | AI Search service → Keys → Primary admin key |
| `AZURE_STORAGE_CONNECTION_STRING` | Storage account → Access keys → Connection string (key1) |

If your organization fronts Azure OpenAI with API Management and requires Azure AD, set `AUTH_TENANT_ID`, `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`, and `AUTH_SCOPE`. In that mode, the app sends both the subscription key and a bearer token and refreshes the token automatically before expiry.

### 5. Create blob containers (once)

If `resumes` and `jds` containers do not exist yet, create them in the Azure Portal:

**Storage account → Containers → + Container**
- Name: `resumes` | Access level: Private
- Name: `jds` | Access level: Private

Or via Azure CLI in PowerShell:

```powershell
az storage container create --name resumes --connection-string $env:AZURE_STORAGE_CONNECTION_STRING
az storage container create --name jds    --connection-string $env:AZURE_STORAGE_CONNECTION_STRING
```

### 6. Run the app

```powershell
streamlit run app.py
```

The app opens in your browser at `http://localhost:8501`.
The Azure Cognitive Search index (`resume_chunks`) is **auto-created on first run** — no portal setup needed.

---

## Usage

### Upload resumes
1. Go to the **Upload** tab → **Resumes** column
2. Select one or more `.txt`, `.pdf`, or `.docx` files
3. Click **Upload & Index Resumes**
   - Each file is stored in blob storage and immediately chunked, embedded, and indexed

### Upload a job description
1. Go to the **Upload** tab → **Job Descriptions** column
2. Select a `.txt`, `.pdf`, or `.docx` JD file
3. Click **Upload JDs**

### Rank resumes
1. Go to the **Rank Resumes** tab
2. Select a JD from the dropdown
3. Click **Rank Resumes**
4. View the ranked table and expand any candidate to see the per-category score breakdown

---

## Project structure

```
ResumeRankerPOC1/
├── app.py                        <- Streamlit UI and ranking pipeline
├── search_utils.py               <- Chunking, embedding, index management, hybrid search
├── .env.example                  <- Copy to .env and fill in values
├── requirements.txt
├── README.md
└── Resume-Ranker-POC/
    ├── resumes/                  <- Sample resume files (.txt)
    └── jds/                      <- Sample job description files (.txt)
```

---

## Troubleshooting

**Execution policy error when activating venv**
Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` in PowerShell (no admin needed).

**`No module named 'search_utils'`**
Run `streamlit run app.py` from the `ResumeRankerPOC1` root directory, not from a subdirectory.

**Index creation fails**
Free tier Azure Cognitive Search does not support vector search. Use Basic or higher.

**Empty results after ranking**
- Confirm resumes were uploaded and indexed (green success message in Upload tab)
- Confirm both blob containers exist
- Check that `EMBEDINGS_OPENAI_DEPLOYMENT_NAME` in `.env` matches your actual embedding deployment name

**`401 Unauthorized` or `Invalid Azure AD JWT` from Azure OpenAI**
- If `OPENAI_ENDPOINT` points to an APIM or internal proxy endpoint, configure `AUTH_TENANT_ID`, `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`, and `AUTH_SCOPE`
- Keep `OPENAI_API_KEY` populated if your gateway expects a subscription key in addition to the bearer token
- Restart Streamlit after changing `.env` so cached clients are rebuilt with the new auth settings

**`ResourceNotFoundError` on blob operations**
The `resumes` or `jds` container does not exist — create them as described in Step 5.
