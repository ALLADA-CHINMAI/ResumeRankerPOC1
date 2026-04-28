# Resume Ranker POC

An AI-powered resume screening tool. Give it a job description and it ranks all resumes in your Azure Blob Storage by match score.

---

## What it does

1. Reads a Job Description (JD) from Azure Blob Storage
2. Uses Azure OpenAI (GPT-4o) to extract the key skills, titles, and requirements from the JD
3. Uses Azure AI Search to retrieve the top 30 most relevant resume chunks
4. Scores each resume with GPT-4o on a 0–100 scale
5. Combines both scores and displays a ranked table in the Streamlit UI

**Final score formula:** `0.9 × GPT_Score + 0.1 × AI_Search_Score`

---

## Architecture

```
Azure Blob Storage
├── jds/             ← Job Description files (.txt)
└── resumes/         ← Resume files (.txt, .pdf, .docx)
        │
        ▼
Azure AI Search (Index + Indexer)
        │
        ▼
Streamlit App  ──►  Azure OpenAI (GPT-4o)
        │
        ▼
Ranked results table
```

---

## Chunking, Embedding, Indexing, and Search — what happens where

| Step | Done by | Where |
|---|---|---|
| **Chunking** | Azure AI Search indexer | Automatically splits resume documents into smaller chunks when it crawls the Blob container |
| **Embedding** | Azure AI Search indexer (built-in vectorization) | Each chunk is embedded into a vector during indexing |
| **Indexing** | Azure AI Search indexer | Chunks + vectors are stored in the search index. The indexer runs on upload or on demand |
| **Search** | Azure AI Search (`SearchClient`) | Text search against the index using GPT-extracted JD keywords. Returns the top 30 matching chunks with a relevance score (`@search.score`) |
| **AI Scoring** | Azure OpenAI GPT-4o | Each returned chunk is sent to GPT-4o with the JD. GPT returns a 0–100 score based on experience, skills, education, achievements, and keywords |

> The app does **not** do its own chunking or embedding in Python. Both are delegated entirely to the Azure AI Search indexer configured in the Azure portal.

---

## Setup

### Prerequisites

- Python 3.10+
- Azure resources already created:
  - Blob Storage account with containers `jds` and `resumes`
  - Azure AI Search service, index, and indexer (with vectorization enabled)
  - Azure OpenAI deployment (GPT-4o)

### Install dependencies

```powershell
pip install -r requirements.txt
```

### Set environment variables

Create a `.env` file or set these in your shell before running:

```
CONNECTION_STRING=<Azure Blob Storage connection string>
CONTAINER_NAME_RESUME=resumes
ENDPOINT_URL=<Azure OpenAI endpoint>
DEPLOYMENT_NAME=gpt-4o
OPENAI_API_KEYa=<Azure OpenAI API key>
SEARCH_SERVICE_ENDPOINT=<Azure AI Search endpoint>
INDEX_NAME=<your index name>
QUERY_API_KEY=<Azure AI Search query key>
```

> Note: the OpenAI key variable is named `OPENAI_API_KEYa` (with trailing `a`) — match this exactly.

---

## Upload resumes and JDs to Azure

Before running the app, upload your local files to Blob Storage using the upload script:

```powershell
set LOCAL_DIRS_JDS=Resume-Ranker-POC\jds
set LOCAL_DIRS_RESUMES=Resume-Ranker-POC\resumes
python Resume-Ranker-POC\upload_blobs.py
```

---

## Run the app

```powershell
streamlit run Resume-Ranker-POC\Demo\app.py
```

Open the local Streamlit URL shown in the terminal.

---

## How the app works

**On startup:**
- Connects to Blob Storage, AI Search, and Azure OpenAI
- Loads the JD (`Job Listing Detail_sn.txt`) from the `jds` container
- Runs `GetResults()` — extracts JD keywords via GPT, searches for top 30 resumes, scores each one, displays ranked table

**On file upload:**
- Uploads the file to the `resumes` Blob container
- Triggers the AI Search indexer to re-index (chunking + embedding happens here)
- Re-runs `GetResults()` and shows the updated ranking

---

## Project structure

```
ResumeRankerPOC1/
├── Resume-Ranker-POC/
│   ├── Demo/
│   │   └── app.py                      ← Main Streamlit app
│   ├── jds/                            ← Sample job description files
│   ├── resumes/                        ← Sample resume files
│   ├── upload_blobs.py                 ← One-time upload utility
│   ├── fetch-AI-search_integrated2.py  ← CLI version with detailed per-category scoring
│   └── hybrid-search.py               ← Hybrid text + vector search experiment
├── testing text extractor.py          ← Utility: extract text from PDF/DOCX
├── requirements.txt
└── README.md
```
