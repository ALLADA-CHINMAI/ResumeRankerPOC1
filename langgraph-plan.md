Explore resume ranking codebase

Read [](file:///c%3A/Users/allada.chinmai/OneDrive%20-%20Providence%20St.%20Joseph%20Health/Documents/Repos/ResumeRankerPOC/resumeRanking/app.py#1-1), lines 1 to 500

Read [](file:///c%3A/Users/allada.chinmai/OneDrive%20-%20Providence%20St.%20Joseph%20Health/Documents/Repos/ResumeRankerPOC/resumeRanking/search_utils.py)

Read [](file:///c%3A/Users/allada.chinmai/OneDrive%20-%20Providence%20St.%20Joseph%20Health/Documents/Repos/ResumeRankerPOC/resumeRanking/requirements.txt)

Read [](file:///c%3A/Users/allada.chinmai/OneDrive%20-%20Providence%20St.%20Joseph%20Health/Documents/Repos/ResumeRankerPOC/resumeRanking/README.md)

Read [](file:///c%3A/Users/allada.chinmai/OneDrive%20-%20Providence%20St.%20Joseph%20Health/Documents/Repos/ResumeRankerPOC/resumeRanking/app.py#500-500), lines 500 to 524

Read [](file:///c%3A/Users/allada.chinmai/OneDrive%20-%20Providence%20St.%20Joseph%20Health/Documents/Repos/ResumeRankerPOC/resumeRanking/Resume-Ranker-POC/resumes/0_percent_ATS_resume.txt)

Read [](file:///c%3A/Users/allada.chinmai/OneDrive%20-%20Providence%20St.%20Joseph%20Health/Documents/Repos/ResumeRankerPOC/resumeRanking/Resume-Ranker-POC/jds/Job%20Listing%20Detail_fin.txt)

Read [](file:///c%3A/Users/allada.chinmai/AppData/Roaming/Code/User/workspaceStorage/8eff4555e0bd6b89643671be64e82a36/GitHub.copilot-chat/chat-session-resources/79a409dc-18a3-4c2b-80f1-f74b0a5b35ac/toolu_bdrk_01W2pyHYJnRPAj5MjZ8pYdRL__vscode-1778763076748/content.txt#1-1), lines 1 to 200

Analyzing your answers...

Analyzing your answers...

I now have a full picture of the codebase and your answers. Let me build the plan. 

Created memory file [](file:///c%3A/Users/allada.chinmai/AppData/Roaming/Code/User/workspaceStorage/8eff4555e0bd6b89643671be64e82a36/GitHub.copilot-chat/memory-tool/memories/NzlhNDA5ZGMtMThhMy00YzJiLTgwZjEtZjc0YjBhNWIzNWFj/plan.md)

---

## Plan: Enhanced Resume Ranker — LangGraph + Skills Gap + Azure Production

### TL;DR
Replace the linear GPT-4o pipeline with a **LangGraph multi-agent state machine** that adds a **Team Skills Gap Analyst** (deterministic SQL, no LLM cost) as a new scoring dimension, keeps Azure Cognitive Search + OpenAI as backends, uses GPT-4o-mini for cheap JD parsing, and stays on Streamlit with an env flag to gate the new pipeline.

---

> **Answering your skills filter question first:**
> The best corporate strategy is **Gap Filling + Baseline Compatibility**. Hiring someone who purely duplicates your team's skills creates redundancy. The sweet spot is:
> 1. **Must have**: JD-required skills (baseline)
> 2. **High value**: Skills the JD requires that your current team *lacks* (gap fill — most impactful hire)
> 3. **Good signal**: Some overlap with team's existing skills (can collaborate, shared toolchain)
> 4. **Bonus**: Extra skills beyond what the JD asks (additive value)

---

### Phase 1 — LangGraph Orchestration + Skills Gap Integration (MVP)

**Step 1: Project scaffolding** — add `langchain`, `langgraph`, `pyodbc`/`SQLAlchemy`, `azure-monitor-opentelemetry` to requirements.txt. Add env vars: `AZURE_SQL_CONNECTION_STRING`, `USE_LANGGRAPH=true`

**Step 2: Pydantic state models** — create `models.py` with `RankingState` TypedDict containing fields: `jd_text`, `parsed_jd` (role, required_skills, nice_to_have_skills, experience_level, domain), `team_skill_profile` (coverage dict, gap_skills, weighted_priorities), `resume_chunks`, `ranked_results`

**Step 3: JD Parser Agent node** — `langgraph_pipeline.py::parse_jd_node` — uses GPT-4o-mini with `with_structured_output(ParsedJD)` to extract structured fields. Replaces the ad-hoc keyword extraction prompt in app.py. Cheap and fast, single call.

**Step 4: SQL Skills Connector** — `sql_skills_client.py` — wraps `pyodbc`/SQLAlchemy; queries `caregiver` JOIN `skills` WHERE `role LIKE '%{extracted_role}%'`. Returns skill frequency map for the team in that role. All deterministic — **zero LLM calls**.

**Step 5: Skills Gap Analyst node** — `langgraph_pipeline.py::skills_gap_node` — deterministic logic:
- Computes `team_skill_coverage` (% of candidates on team with each skill)
- `gap_skills` = (JD required skills) WHERE team coverage < 50%
- `weighted_skill_priorities` = gap skills get weight 1.5x, common skills 1.0x, redundant skills 0.7x

**Step 6: Resume Retriever node** — thin wrapper around existing `search_utils.py::ResumeSearchClient.hybrid_search()`. Refactored as a `@tool` in LangChain.

**Step 7: Enhanced Scorer node** — `langgraph_pipeline.py::score_resumes_node` — GPT-4o scores each resume against JD + receives `weighted_skill_priorities` as context. New rubric adds **Gap Fill Score** (25%) and **Team Compatibility Score** (15%) alongside existing categories.

**Step 8: Result Aggregator node** — deterministic: combines sub-scores into final weighted score, sorts, outputs `ranked_results`.

**Step 9: Wire the LangGraph** — `langgraph_pipeline.py::build_graph()` — nodes connected sequentially with conditional edges (e.g., skip skills gap node if SQL is unavailable → graceful degradation).

**Step 10: Update app.py** — gate with `USE_LANGGRAPH` env flag. If true, call `build_graph().invoke(state)`. Add new UI section: "Team Skill Context" showing current team skill coverage heatmap before the ranked results. Update results cards to show gap-fill contribution per candidate.

---

### Phase 2 — Skill Synonym Normalization + Healthcare Ontology

**Step 11: Skill synonym table** — Add a `skill_synonyms` table in Azure SQL mapping variants → canonical form (e.g., `"Epic EMR" → "Epic"`, `"RN" → "Registered Nurse"`, `"EHR" → "Electronic Health Records"`). Pre-seed with 50–100 healthcare-domain terms (SNOMED-CT, nursing skills taxonomy).

**Step 12: `skill_normalizer.py`** — normalizes skill tokens from both resumes and the SQL skills table to canonical form before comparison. Deterministic string matching + fuzzy match fallback (using `rapidfuzz`, no LLM). This prevents missing matches like `"React.js"` ≠ `"ReactJS"`.

**Step 13: Add skill freshness scoring** — parse employment dates from resume text (heuristic regex, deterministic). Skills used within the last 3 years get a recency multiplier. Skills from 5+ years ago down-weighted. Zero LLM cost.

**Step 14: Career trajectory check** — deterministic heuristic: flag frequent job hopping (>3 jobs in 3 years), check for progression pattern (title advancement), surface as metadata in results card.

---

### Phase 3 — MCP Tool Layer

**Step 15: Define MCP tool interfaces** — wrap SQL skills query, Azure Search hybrid search, and Blob resume reader as MCP tool definitions. Agents call tools via the MCP protocol rather than direct API calls. This makes the pipeline extensible — future agents plug in without touching existing node code.

**Step 16: Interview Question Generator** — add an optional post-ranking node: given top 3 candidates and their identified skill gaps vs. JD, auto-generate 5 targeted interview questions per candidate using GPT-4o-mini. Single LLM call per candidate, only triggered on demand.

---

### Phase 4 — Azure Production Deployment

**Step 17: FastAPI wrapper** — `api.py` — thin REST API wrapping the LangGraph pipeline: `POST /rank` accepts JD blob name + list of resume blob names, returns ranked JSON. Keeps Streamlit calling it via `requests` internally (decouples UI from pipeline).

**Step 18: Containerize** — `Dockerfile` — Python 3.11 slim, installs requirements, exposes Streamlit on 8501. Single-container: both Streamlit and FastAPI run in the same image (startup script). `docker-compose.yml` for local dev.

**Step 19: Azure Container Apps** — one Container App for the UI/API, auto-scales to 0 between uses (cost-efficient for internal tool). Environment variables injected from Azure Key Vault references.

**Step 20: Key Vault + Application Insights** — migrate all secrets from .env to Azure Key Vault. Use `azure-monitor-opentelemetry` SDK to track: LLM token usage per request, ranking latency per phase, Azure Search query cost — visible in Application Insights.

---

### Relevant Files

- app.py — add LangGraph routing, `USE_LANGGRAPH` gate, team skill heatmap UI, updated results cards
- search_utils.py — refactor `hybrid_search` and `get_resume_text` as LangChain `@tool` wrappers
- requirements.txt — add `langchain`, `langgraph`, `pyodbc`, `sqlalchemy`, `rapidfuzz`, `azure-monitor-opentelemetry`
- NEW `models.py` — Pydantic/TypedDict models for LangGraph state
- NEW `langgraph_pipeline.py` — the full multi-agent graph
- NEW `sql_skills_client.py` — Azure SQL connector for employee skills
- NEW `skill_normalizer.py` — synonym normalization + fuzzy matching

---

### New Scoring Rubric (vs. current)

| Dimension | Current | New | Method |
|---|---|---|---|
| JD Technical Skills Match | 30 pts | 25 pts | LangChain RAG (GPT-4o) |
| Team Skill Gap Fill | — | 25 pts | Deterministic SQL |
| Team Compatibility | — | 15 pts | Deterministic SQL |
| Experience Depth | 25 pts | 15 pts | GPT-4o |
| Certifications | 15 pts | 10 pts | GPT-4o |
| Education | 10 pts | 5 pts | GPT-4o |
| Skill Freshness | — | 5 pts | Deterministic date parsing |
| **Total** | **100** | **100** | |

---

### Verification

1. Run unit tests for `skills_gap_node` with a mock SQL result set — verify gap/compatibility math
2. Run full pipeline with existing sample resumes + JDs in resumes — compare new scores vs. old to confirm rankings make sense
3. Test `USE_LANGGRAPH=false` path — verify old pipeline still works as fallback
4. Test SQL connector with a read-only service account against a staging copy of the skills DB
5. Deploy Container App to Azure dev environment, verify secrets load from Key Vault, token usage appears in Application Insights

---

### Decisions
- **No full knowledge graph in MVP**: Cosmos DB Gremlin adds infra complexity without proportional value at 500–5000 employee scale. Phase 3 can upgrade to graph if multi-hop skill inference (e.g., "has Python → can do data analysis") is needed.
- **Streamlit stays**: Healthcare internal tool, no need for React. Feature flag `USE_LANGGRAPH` allows safe rollout.
- **LLM calls minimized**: Only 2 LLM calls in the new pipeline — JD parsing (GPT-4o-mini, cheap) + resume scoring (GPT-4o, batched). All skill gap math is deterministic SQL.

---

### Additional Features Worth Adding (Prioritized)

1. **Blind Scoring Mode** — strip candidate names before scoring → DEI/bias compliance (easy win, 1-day effort)
2. **Hiring Outcome Feedback Loop** — store `(resume_id, jd_id, final_score, was_hired, 90_day_performance)` in SQL → after 6 months, re-tune scoring weights with actual outcomes
3. **Team Skill Heatmap Dashboard** — standalone tab showing current team's skill coverage per role (reads from SQL, no LLM) — useful for HR regardless of hiring need
4. **Batch Async Processing via Azure Service Bus** — for ranking 200+ resumes, decouple the UI from the pipeline so the user doesn't wait; results emailed/notified when done

---

Want me to start with **Phase 1**, or do you want to adjust the scoring weights or the SQL query approach first?