# Resume Ranker

Resume Ranker is an AI-assisted resume evaluation system built on Azure services using Model Context Protocol (MCP).

This repository contains only the **MCP server** — a unified backend that exposes resume ranking via both REST API and MCP protocol.

- **UI Frontend**: Separate Angular repository (not included here)
- **Azure Functions**: Moved to separate repo
- **Backend**: Unified in `src/` with API and MCP modes

## Quick Start

### Prerequisites
- Python 3.10+
- Azure credentials in `local.settings.json`

### Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Run API

```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

Visit: `http://localhost:8000/docs` for interactive API docs

### Run MCP Server

**STDIO mode** (default for tools):
```bash
python -m src.mcp.main --transport stdio
```

**HTTP mode**:
```bash
python -m src.mcp.main --transport http --port 8001
```

## Project Structure

```text
Resume_Ranker/
├── src/
│   ├── __init__.py
│   ├── api/                          # FastAPI REST endpoints
│   │   ├── __init__.py
│   │   └── main.py                   # POST /rankResumes endpoint
│   ├── mcp/                          # Model Context Protocol server
│   │   ├── __init__.py
│   │   ├── main.py                   # Entry point (launched by tools)
│   │   ├── server.py                 # FastMCP server with tools
│   │   └── server_config.py          # Logging config
│   └── common/                       # Shared business logic
│       ├── __init__.py
│       ├── clients.py                # Azure service singletons
│       ├── config.py                 # Scoring config loader
│       ├── models.py                 # Pydantic data models
│       ├── ranking.py                # Resume ranking pipeline
│       ├── search.py                 # Azure Cognitive Search wrapper
│       ├── storage.py                # Azure Blob Storage helpers
│       └── scoring_config.json       # Scoring rubric and weights
├── local.settings.json               # Azure credentials & config
├── requirements.txt                  # Python dependencies (MCP-only)
├── .env                              # Optional env overrides
├── .env.example                      # Template for local development
├── .gitignore
├── startup.sh                        # Optional launcher script
└── README.md
```

## API Endpoints

### `POST /rankResumes`

Rank existing indexed resumes against a job description.

**Request**:
```json
{
  "req_id": "job123",
  "top_k": 10,
  "natural_language_query": null
}
```

**Response**:
```json
{
  "req_id": "job123",
  "jd_name": "Senior Engineer.txt",
  "jd_blob_url": "https://...",
  "query_text": "...",
  "jd_keywords": {...},
  "results": [
    {
      "rank": 1,
      "candidate_id": "resume_123",
      "overall_score": 92.5,
      "resume_blob_url": "https://..."
    }
  ]
}
```

## MCP Tools

The MCP server exposes the following tools:

- `rankResumes` — rank candidate resumes against a job description
- `analyzeSkillGaps` — identify skill gaps between candidate and JD

## Configuration

All Azure services are configured via environment variables in `local.settings.json`:

- `AZURE_STORAGE_CONNECTION_STRING` — Blob Storage account
- `AZURE_SEARCH_ENDPOINT` — Cognitive Search endpoint
- `AZURE_SEARCH_API_KEY` — Cognitive Search API key
- `OPENAI_ENDPOINT` — Azure OpenAI endpoint
- `OPENAI_API_KEY` — Azure OpenAI API key
- `OPENAI_API_VERSION` — API version (e.g., `2025-01-01-preview`)
- `OPENAI_DEPLOYMENT_NAME` — Deployment name (e.g., `LnD-gpt-4o`)

## Development

**Run tests** (if available):
```bash
pytest
```

**Install local changes** (for development):
```bash
pip install -e .
```

## Architecture

- **API Mode**: FastAPI server for REST clients (Angular UI calls this)
- **MCP Mode**: FastMCP server for Claude and other MCP-compatible clients
- **Common**: Shared ranking logic, Azure clients, and models

