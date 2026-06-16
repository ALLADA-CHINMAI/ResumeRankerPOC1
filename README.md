# Resume Ranker

Resume Ranker is an AI-assisted resume evaluation system built on Azure services.

This repository now uses a single backend root and a decoupled frontend:
- `ResumeRankerMCP/` -> backend code (common, api, mcp)
- `ResumeRankerFrontend/` -> Streamlit UI (API integration only)
- `ResumeRankerFunctions/` -> separate Function App scaffolding (self-contained, no backend/common imports)

## Current Structure

```text
resumeRanking/
├── ResumeRankerMCP/
│   ├── common/
│   ├── api/
│   └── mcp/
├── ResumeRankerFrontend/
│   └── app.py
├── ResumeRankerFunctions/
│   ├── function_app.py
│   ├── resume_trigger.py
│   └── jd_trigger.py
├── requirements.txt
└── startup.sh
```

## Run Commands

### 1) API

```bash
uvicorn ResumeRankerMCP.api.main:app --host 0.0.0.0 --port 8000
```

or

```bash
./startup.sh
```

### 2) MCP Server

```bash
python -m ResumeRankerMCP.mcp.main --transport stdio
```

HTTP mode:

```bash
python -m ResumeRankerMCP.mcp.main --transport http --port 8000
```

### 3) Frontend (API-only)

Set API base URL (optional, default is `http://localhost:8000`):

- PowerShell:

```powershell
$env:BACKEND_API_BASE_URL = "http://localhost:8000"
streamlit run ResumeRankerFrontend/app.py
```

## Notes

- Frontend no longer imports backend internals; it calls API endpoints only.
- Functions are intentionally kept separate and self-contained for later extraction.
- Legacy versioned READMEs were removed to keep this file as the single documentation source.
