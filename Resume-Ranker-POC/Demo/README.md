# Resume Ranker POC (Demo)

This document explains installation, setup, and the end-to-end flow for the Streamlit app in this folder.

## 1) What this app does

The app in [Resume-Ranker-POC/Demo/app.py](Resume-Ranker-POC/Demo/app.py) ranks resumes against a Job Description (JD) using:
- Azure Blob Storage (JD + resume files)
- Azure AI Search (retrieval score)
- Azure OpenAI (LLM relevance score)
- Streamlit UI (upload + display ranking)

Final score formula currently used:
- Final Score = `0.9 * AI_GPT_Score + 0.1 * AI_Search_Score`

---

## 2) Prerequisites

- Python 3.10+
- Azure resources already created:
  - Blob Storage account with containers:
    - `jds`
    - `resumes`
  - Azure AI Search service + index + indexer
  - Azure OpenAI deployment (`gpt-4o` in current code)

---

## 3) Installation setup

From workspace root (`resumeRanking`) create/activate environment and install packages.

### Required packages

- `streamlit`
- `azure-storage-blob`
- `azure-search-documents`
- `azure-core`
- `openai`
- `tabulate`
- `pandas`

Install them:

```powershell
pip install streamlit azure-storage-blob azure-search-documents azure-core openai tabulate pandas
```

---

## 4) Configuration

### Current implementation behavior

The app contains hardcoded values in [Resume-Ranker-POC/Demo/app.py](Resume-Ranker-POC/Demo/app.py#L26-L53) for:
- Blob connection string
- Search endpoint/index/key
- Azure OpenAI endpoint/deployment/key fallback

### Recommended configuration (safer)

Move secrets to environment variables before running:

- `ENDPOINT_URL`
- `DEPLOYMENT_NAME`
- `OPENAI_API_KEYa` (note: code currently uses this exact name)
- `AZURE_STORAGE_CONNECTION_STRING`
- `AZURE_SEARCH_ENDPOINT`
- `AZURE_SEARCH_INDEX_NAME`
- `AZURE_SEARCH_API_KEY`

If you keep current code unchanged, it will still use hardcoded defaults where variables are missing.

---

## 5) How to run

From `resumeRanking` root:

```powershell
streamlit run Resume-Ranker-POC/Demo/app.py
```

Then open the local Streamlit URL shown in terminal.

---

## 6) Functional flow (what happens internally)

### Startup flow

1. App initializes Azure clients:
   - Blob client
   - Search client
   - Search indexer client
   - Azure OpenAI client
2. App reads JD text from Blob:
   - Container: `jds`
   - Blob: `Job Listing Detail_sn.txt`

### Ranking flow (`GetResults()`)

1. `MainContent(jd_text)` sends JD to Azure OpenAI.
2. Model returns extracted/optimized JD content for search.
3. App queries Azure AI Search with this text (`top=30`).
4. For each search result chunk:
   - `AIScore(jd_text, resume_chunk)` asks Azure OpenAI for numeric score out of 100.
   - Retries until parseable float is returned.
5. App combines:
   - Search score (`@search.score`)
   - GPT score
6. App computes weighted final score and sorts descending.
7. Streamlit displays ranked table (`Name`, `Final Score`).

### Upload flow

1. User uploads a file via Streamlit uploader.
2. File is uploaded to Blob container `resumes`.
3. App runs indexer `rag-1751366501876-indexer`.
4. App recalculates ranking by calling `GetResults()`.
5. Updated ranked table is shown.

---

## 7) Notes and known issues

- `GetResults()` is called at startup and after upload; this can be slow/costly due to repeated LLM calls.
- Secrets are in source code; move to environment variables for production.
- `OPENAI_API_KEYa` variable name includes trailing `a` in current code.
- `AIScore()` expects only numeric output; non-numeric model responses are retried.
- Search index and indexer names are fixed in code.

---

## 8) Suggested next improvements

- Add `.env` support and remove hardcoded secrets.
- Add caching for JD extraction and resume scoring.
- Batch and deduplicate resume scoring.
- Add robust error handling for Blob/Search/OpenAI failures.
- Add a proper `requirements.txt` and root project `README.md`.
