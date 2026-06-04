# Resume Ranker — v2 Update: MCP Server

This document covers what was added in v2. For the original setup, architecture, and Streamlit UI documentation, see [README.md](README.md).

---

## What's New in v2

A **Model Context Protocol (MCP) server** has been added as a new module alongside the existing Streamlit UI. It exposes the `ResumeRankerCore` business logic as structured tools that any MCP-compatible AI agent can call — including **GitHub Copilot (VSCode Agent mode)**, **Claude Desktop**, or custom agents.

The Streamlit UI is **untouched**. The MCP server is purely additive.

---

## New Files

```
ResumeRankerPOC1/
├── ResumeRankerMCP/               ← New module (Python 3.10+ required)
│   ├── __init__.py
│   ├── main.py                    ← FastMCP server entrypoint (stdio + HTTP)
│   ├── server_config.py           ← Logging setup (stderr-only for stdio safety)
│   ├── test_tools.py              ← Direct Azure test script (no MCP protocol needed)
│   └── tools/
│       ├── __init__.py
│       ├── catalog.py             ← list_candidates, list_job_descriptions
│       ├── search.py              ← search_candidates, get_jd_text
│       ├── ranking.py             ← rank_candidates_for_job, compare_candidates
│       └── profile.py             ← get_candidate_profile, analyze_skill_gaps
├── .vscode/
│   └── mcp.json                   ← GitHub Copilot MCP registration
└── requirements.txt               ← Added: fastmcp>=2.0.0, uvicorn>=0.29.0
```

---

## The 8 Tools

Tools are automatically discovered by the agent from their docstrings. The agent reads the description to decide which tool to call.

| Tool | What it does | Cost |
|---|---|---|
| `list_candidates` | List all indexed resume filenames | Free |
| `list_job_descriptions` | List all stored JD filenames | Free |
| `search_candidates` | Hybrid BM25+vector search — fast exploration | Free |
| `get_jd_text` | Fetch full text of a stored JD by filename | Free |
| `rank_candidates_for_job` | GPT-4o 6-dimension ranking against a JD (top 100 pts) | GPT-4o (capped at 15 candidates) |
| `compare_candidates` | Side-by-side GPT-4o scoring of specific candidates | GPT-4o per candidate |
| `get_candidate_profile` | Extract structured profile: skills, experience, certs | GPT-4o once |
| `analyze_skill_gaps` | Strengths + gaps + hire recommendation vs. a JD | GPT-4o once |

### Scoring Rubric (used by `rank_candidates_for_job` and `compare_candidates`)

| Dimension | Max Points | What it evaluates |
|---|---|---|
| experience | 35 | Years + depth of relevant work history |
| technicalSkills | 40 | Coverage of required skills, tools, languages |
| certifications | 5 | Required or preferred certifications |
| education | 5 | Degree, field, level |
| location | 5 | Geographic fit or remote readiness |
| domainFit | 10 | Industry/domain alignment |
| **Total** | **100** | |

### Example Agent Queries → Tools Used

| Agent asks | Tools called |
|---|---|
| "Top 5 candidates for a senior Python engineer with 5+ years" | `rank_candidates_for_job` |
| "Search for candidates with Kubernetes and AWS experience" | `search_candidates` |
| "Compare Alice Jones and John Smith for this DevOps role" | `compare_candidates` |
| "What resumes do we have in the system?" | `list_candidates` |
| "Give me a structured profile of john_smith.pdf" | `get_candidate_profile` |
| "Why didn't this candidate score higher — what are their gaps?" | `analyze_skill_gaps` |
| "Rank all resumes against the senior_swe_jd.pdf we have on file" | `get_jd_text` → `rank_candidates_for_job` |

---

## Prerequisites

- **Python 3.10+** — FastMCP requires Python 3.10 or later (the Streamlit UI continues to run on any Python version)
- All existing Azure environment variables (unchanged from v1 — see `.env.example`)

Install the new dependencies:

```bash
pip install fastmcp>=2.0.0 uvicorn>=0.29.0
# or install everything:
pip install -r requirements.txt
```

---

## How to Run the MCP Server

```bash
# From ResumeRankerPOC1/
python -m ResumeRankerMCP.main --transport http --port 8000
# Clients connect to: http://localhost:8000/sse
```

### Environment variables

The MCP server uses the same `.env` file as the Streamlit UI. One optional addition:

```env
LOG_LEVEL=INFO   # DEBUG | INFO | WARNING | ERROR
```

---

## How to Test

### Option A — Direct tool tests (no MCP protocol, hits live Azure)

Run from `ResumeRankerPOC1/`:

```bash
# Run all tests
python -m ResumeRankerMCP.test_tools

# Run a specific test section
python -m ResumeRankerMCP.test_tools --test catalog
python -m ResumeRankerMCP.test_tools --test search
python -m ResumeRankerMCP.test_tools --test rank
python -m ResumeRankerMCP.test_tools --test profile
python -m ResumeRankerMCP.test_tools --test gaps
```

This validates Azure connectivity and each tool function before involving any MCP client.

### Option B — Quick HTTP smoke test

```bash
# Terminal 1: start the server
python -m ResumeRankerMCP.main --transport http --port 8000

# Terminal 2: verify the SSE endpoint responds
curl http://localhost:8000/sse
```

### Option C — MCP Inspector (interactive UI)

```bash
# Terminal 1: start the server
python -m ResumeRankerMCP.main --transport http --port 8000

# Terminal 2: launch the inspector
npx @modelcontextprotocol/inspector http://localhost:8000/sse
```

Opens a browser UI at `http://localhost:5173` where you can browse all tools, fill in arguments, and call them interactively — no client code needed.

---

## Architecture Notes

- **No ingestion via MCP** — Resumes and JDs are uploaded through the Streamlit UI. The MCP server is query-only.
- **Docstrings = tool descriptions** — FastMCP converts each function's docstring into the `description` field the agent reads from `tools/list`. The docstrings include usage examples so the agent can select the right tool.
- **Cost cap** — `rank_candidates_for_job` uses the same two-stage pipeline as the Streamlit UI: GPT-4o is only called on the top 15 candidates from hybrid search, regardless of corpus size.
- **Python 3.10+ scoped** — Only `ResumeRankerMCP/` requires Python 3.10+. `ResumeRankerCore` and `ResumeRankerFrontend` are unchanged.

---

## Next Steps

- UI improvements to `ResumeRankerFrontend/app.py` (planned)
- Additional MCP tools as new use cases emerge
