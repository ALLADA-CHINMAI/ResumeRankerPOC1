"""
LangGraph resume ranking pipeline.

Nodes:
  parse_jd → search_resumes → fetch_history → rerank → END

parse_jd      : extract role/skills/domain from JD text
search_resumes: hybrid BM25+vector search → top candidates
fetch_history : load interview history from candidate_data.xlsx
rerank        : Azure OpenAI reranking with interview context
"""

import json
import logging
from typing import List

from langgraph.graph import StateGraph, END

from ResumeRankerCore.clients import get_openai_client, get_resume_search, OPENAI_DEPLOYMENT
from ResumeRankerCore.models import RankingState
from ResumeRankerMCP.candidate_history import get_by_doc_names

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node 1 — JD Parser
# ---------------------------------------------------------------------------

def parse_jd_node(state: RankingState) -> RankingState:
    state["progress_messages"].append("Parsing job description…")
    try:
        resp = get_openai_client().chat.completions.create(
            model=OPENAI_DEPLOYMENT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract structured info from the job description. "
                        "Return JSON with keys: role (str), required_skills (list[str]), "
                        "nice_to_have_skills (list[str]), experience_level (junior|mid|senior), "
                        "domain (str), certifications (list[str]), location (str). "
                        "No markdown."
                    ),
                },
                {"role": "user", "content": state["jd_text"]},
            ],
            max_tokens=600,
            temperature=0,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(resp.choices[0].message.content)
        state["parsed_jd"] = parsed
        state["progress_messages"].append(
            f"JD parsed — role: {parsed.get('role', '?')}, "
            f"{len(parsed.get('required_skills', []))} required skills."
        )
    except Exception as exc:
        logger.warning("JD parse failed: %s", exc)
        state["parsed_jd"] = None
    return state


# ---------------------------------------------------------------------------
# Node 2 — Hybrid Search
# ---------------------------------------------------------------------------

def search_resumes_node(state: RankingState) -> RankingState:
    parsed = state.get("parsed_jd")
    if parsed:
        query = " ".join([parsed.get("role", "")] + parsed.get("required_skills", []))[:1000]
    else:
        query = state["jd_text"][:500]

    state["progress_messages"].append("Running hybrid search across resumes…")
    chunks = get_resume_search().hybrid_search(
        query,
        top=50,
        filter_names=state.get("selected_resumes") or None,
    )

    if not chunks:
        state["progress_messages"].append("No matching resumes found.")
        state["resume_candidates"] = []
        return state

    scores: dict = {}
    for chunk in chunks:
        name = chunk["doc_name"]
        scores[name] = scores.get(name, 0.0) + chunk["search_score"]

    top = sorted(scores.items(), key=lambda x: -x[1])[:15]
    state["resume_candidates"] = [
        {"doc_name": name, "semantic_score": round(score, 4)} for name, score in top
    ]
    state["progress_messages"].append(
        f"Search complete — {len(scores)} candidates found, top {len(top)} selected."
    )
    return state


# ---------------------------------------------------------------------------
# Node 3 — Fetch Interview History
# ---------------------------------------------------------------------------

def fetch_history_node(state: RankingState) -> RankingState:
    candidates = state.get("resume_candidates", [])
    if not candidates:
        state["candidate_profiles"] = []
        return state

    state["progress_messages"].append("Fetching candidate interview history…")
    doc_names = [c["doc_name"] for c in candidates]
    score_map = {c["doc_name"]: c["semantic_score"] for c in candidates}

    try:
        profiles = get_by_doc_names(doc_names)
        # attach semantic score to each profile
        for p in profiles:
            p["semantic_score"] = score_map.get(p["latest_ai_search_doc_id"], 0.0)
        state["candidate_profiles"] = profiles
        state["progress_messages"].append(
            f"History loaded for {len(profiles)} candidate(s)."
        )
    except FileNotFoundError:
        state["progress_messages"].append("candidate_data.xlsx not found — ranking without history.")
        # fall back: build minimal profiles from search results only
        state["candidate_profiles"] = [
            {
                "candidate_id": c["doc_name"],
                "full_name": c["doc_name"],
                "latest_ai_search_doc_id": c["doc_name"],
                "semantic_score": c["semantic_score"],
                "interview_history": [],
            }
            for c in candidates
        ]
    return state


# ---------------------------------------------------------------------------
# Node 4 — Rerank
# ---------------------------------------------------------------------------

_RERANK_SYSTEM = """
You are an expert technical recruiter. Rank the candidates for the job description.

Consider: semantic search score, years of experience, role fit, and past interview history
(how far they progressed, rejection reasons, interview scores).

Return valid JSON:
{"ranked_candidates": [{"candidate_id": "...", "full_name": "...", "score": <0-100>, "explanation": "...", "resume_blob_url": "..."}]}

Order by score descending. No markdown, no extra text.
"""


def rerank_node(state: RankingState) -> RankingState:
    profiles = state.get("candidate_profiles", [])
    if not profiles:
        state["ranked_results"] = []
        return state

    state["progress_messages"].append(f"Reranking {len(profiles)} candidates with GPT-4o…")
    payload = json.dumps(profiles, indent=2)

    try:
        resp = get_openai_client().chat.completions.create(
            model=OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _RERANK_SYSTEM},
                {
                    "role": "user",
                    "content": f"Job Description:\n{state['jd_text']}\n\nCandidates:\n{payload}",
                },
            ],
            max_tokens=2000,
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)
        ranked: List[dict] = result.get("ranked_candidates", [])
    except Exception as exc:
        logger.error("Rerank failed: %s", exc)
        ranked = []

    state["ranked_results"] = ranked[: state.get("top_n", 10)]
    state["progress_messages"].append(f"Done — {len(state['ranked_results'])} candidates ranked.")
    return state


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _build_graph():
    g = StateGraph(RankingState)
    g.add_node("parse_jd",        parse_jd_node)
    g.add_node("search_resumes",  search_resumes_node)
    g.add_node("fetch_history",   fetch_history_node)
    g.add_node("rerank",          rerank_node)
    g.set_entry_point("parse_jd")
    g.add_edge("parse_jd",       "search_resumes")
    g.add_edge("search_resumes", "fetch_history")
    g.add_edge("fetch_history",  "rerank")
    g.add_edge("rerank",         END)
    return g.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


def rank_resumes_langgraph(
    jd_text: str,
    top_n: int = 10,
    selected_resumes=None,
    on_progress=None,
) -> list:
    initial_state: RankingState = {
        "jd_text": jd_text,
        "parsed_jd": None,
        "resume_candidates": [],
        "candidate_profiles": [],
        "selected_resumes": selected_resumes,
        "top_n": top_n,
        "ranked_results": [],
        "progress_messages": [],
    }
    final_state = _get_graph().invoke(initial_state)
    if on_progress:
        for msg in final_state.get("progress_messages", []):
            on_progress(msg)
    return final_state.get("ranked_results", [])
