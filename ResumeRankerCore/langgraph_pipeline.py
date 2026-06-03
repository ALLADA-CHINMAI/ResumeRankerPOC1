"""
LangGraph multi-agent ranking pipeline.

Nodes run sequentially:
  parse_jd → skills_gap → retrieve_resumes → score_resumes → END

skills_gap is fully deterministic (SQL, no LLM).
score_resumes passes gap context to GPT-4o so candidates who fill team gaps score higher.

Set USE_LANGGRAPH=true in .env to activate this pipeline in place of ranking.py.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from langgraph.graph import StateGraph, END

from ResumeRankerCore.clients import get_openai_client, get_resume_search, OPENAI_DEPLOYMENT
from ResumeRankerCore.models import RankingState
from ResumeRankerCore.sql_skills_client import get_team_skill_profile
from ResumeRankerCore.storage import fetch_parsed_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring rubric — new categories vs. legacy ranking.py
# ---------------------------------------------------------------------------

SCORE_MAX = {
    "experience":        15,
    "technicalSkills":   25,
    "certifications":    10,
    "education":          5,
    "skillFreshness":     5,
    "gapFill":           25,
    "teamCompatibility": 15,
}

_SCORE_SYSTEM = """
You are an expert resume evaluator. Score the resume against the job description.

Categories and their maximum points:
- experience (max 15): Years and depth of relevant work experience matching the JD.
- technicalSkills (max 25): How completely the candidate's skills cover the JD's required technologies.
- certifications (max 10): Presence of certifications explicitly required or preferred by the JD.
- education (max 5): Relevance and level of the candidate's academic background.
- skillFreshness (max 5): Recency of key required skills — skills used in the last 3 years score higher.
- gapFill (max 25): How well the candidate covers PRIORITY SKILLS the current team lacks. If no team data is provided, score based on rarity/depth of required skills.
- teamCompatibility (max 15): Overlap with the team's existing skills — shared toolchain, ability to collaborate. If no team data, score based on breadth.

Return valid JSON with:
  totalScore: integer (sum of all category scores)
  scores: {experience, technicalSkills, certifications, education, skillFreshness, gapFill, teamCompatibility}
  scoringReasons: same keys, each a one-sentence justification

No markdown. No extra text outside the JSON object.
"""


# ---------------------------------------------------------------------------
# Node 1 — JD Parser
# ---------------------------------------------------------------------------

def parse_jd_node(state: RankingState) -> RankingState:
    """Extract structured fields from the JD using GPT-4o (single cheap call)."""
    state["progress_messages"].append("Parsing job description…")
    try:
        resp = get_openai_client().chat.completions.create(
            model=OPENAI_DEPLOYMENT,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract structured info from the job description. "
                        "Return JSON with keys: "
                        "role (str), required_skills (list[str]), nice_to_have_skills (list[str]), "
                        "experience_level (junior|mid|senior), domain (str), "
                        "certifications (list[str]), location (str). "
                        "No markdown. No extra text."
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
            f"{len(parsed.get('required_skills', []))} required skills identified."
        )
    except Exception as exc:
        logger.warning("JD parse failed: %s", exc)
        state["parsed_jd"] = None
    return state


# ---------------------------------------------------------------------------
# Node 2 — Skills Gap Analyst (deterministic, zero LLM cost)
# ---------------------------------------------------------------------------

def skills_gap_node(state: RankingState) -> RankingState:
    """Query SQL for team skill coverage; compute gap and compatibility weights."""
    parsed = state.get("parsed_jd")
    if not parsed:
        state["team_skill_profile"] = None
        return state

    role = parsed.get("role", "")
    domain = parsed.get("domain", "")
    required_skills = [s.lower() for s in parsed.get("required_skills", [])]

    state["progress_messages"].append("Querying team skill coverage…")
    raw_coverage = get_team_skill_profile(role, domain)

    if raw_coverage is None:
        state["progress_messages"].append("No SQL connection — team gap analysis skipped.")
        state["team_skill_profile"] = None
        return state

    coverage = {k.lower(): v for k, v in raw_coverage.items()}

    # gap_skills: JD-required but team has < 50% coverage
    gap_skills = [s for s in required_skills if coverage.get(s, 0.0) < 0.5]

    # Weight multipliers: gaps get boosted, highly redundant skills penalised
    skill_weights: dict = {}
    for skill in required_skills:
        cov = coverage.get(skill, 0.0)
        if cov < 0.5:
            skill_weights[skill] = 1.5   # gap — high value hire
        elif cov > 0.8:
            skill_weights[skill] = 0.7   # already well covered
        else:
            skill_weights[skill] = 1.0

    state["team_skill_profile"] = {
        "skill_coverage": coverage,
        "gap_skills": gap_skills,
        "skill_weights": skill_weights,
    }
    state["progress_messages"].append(
        f"Team gap analysis complete — {len(gap_skills)} priority skill(s) identified."
    )
    return state


# ---------------------------------------------------------------------------
# Node 3 — Resume Retriever (Stage 1 — hybrid search, free)
# ---------------------------------------------------------------------------

def retrieve_resumes_node(state: RankingState) -> RankingState:
    """Hybrid BM25 + vector search; keep top 15 candidates by aggregated score."""
    parsed = state.get("parsed_jd")
    if parsed:
        required = parsed.get("required_skills", [])
        role = parsed.get("role", "")
        query = " ".join([role] + required)[:1000]
    else:
        query = state["jd_text"][:500]

    state["progress_messages"].append("Running hybrid search across resumes…")
    resume_search = get_resume_search()
    chunks = resume_search.hybrid_search(
        query,
        top=50,
        filter_names=state.get("selected_resumes") or None,
    )

    if not chunks:
        state["progress_messages"].append("No matching resumes found in the index.")
        state["resume_chunks"] = []
        return state

    # Aggregate chunk-level search scores per document
    scores_by_resume: dict = {}
    for chunk in chunks:
        name = chunk["doc_name"]
        scores_by_resume[name] = scores_by_resume.get(name, 0.0) + chunk["search_score"]

    top_candidates = sorted(scores_by_resume, key=lambda n: -scores_by_resume[n])[:15]
    state["resume_chunks"] = [{"name": n} for n in top_candidates]
    state["progress_messages"].append(
        f"Search complete — {len(scores_by_resume)} candidate(s) found, "
        f"top {len(top_candidates)} forwarded to GPT-4o scoring."
    )
    return state


# ---------------------------------------------------------------------------
# Node 4 — GPT-4o Scorer (Stage 2 — parallel, paid)
# ---------------------------------------------------------------------------

def score_resumes_node(state: RankingState) -> RankingState:
    """Score top candidates in parallel with GPT-4o, injecting team gap context."""
    candidates = [c["name"] for c in state.get("resume_chunks", [])]
    if not candidates:
        state["ranked_results"] = []
        return state

    # Build the gap context string injected into every scoring prompt
    team_profile = state.get("team_skill_profile")
    gap_context = ""
    if team_profile and team_profile.get("gap_skills"):
        gap_str = ", ".join(team_profile["gap_skills"])
        already_covered = [
            s for s, w in team_profile["skill_weights"].items() if w < 1.0
        ]
        covered_str = ", ".join(already_covered) if already_covered else "none"
        gap_context = (
            f"\n\nTEAM SKILL CONTEXT (use for gapFill and teamCompatibility scoring):\n"
            f"Priority skills — team currently lacks these (high gapFill if candidate has them): {gap_str}\n"
            f"Well-covered skills — team already has these (lower gapFill value, but good teamCompatibility): {covered_str}"
        )

    resume_search = get_resume_search()

    def _score_one(name: str) -> Optional[dict]:
        try:
            resume_text = fetch_parsed_text(name)
        except Exception:
            logger.warning("Parsed text missing for '%s', reconstructing from search index.", name)
            resume_text = resume_search.get_document_text(name)

        for attempt in range(3):
            try:
                resp = get_openai_client().chat.completions.create(
                    model=OPENAI_DEPLOYMENT,
                    messages=[
                        {"role": "system", "content": _SCORE_SYSTEM},
                        {
                            "role": "user",
                            "content": (
                                f"Job Description:\n{state['jd_text']}"
                                f"{gap_context}\n\n"
                                f"Resume:\n{resume_text[:15000]}"
                            ),
                        },
                    ],
                    max_tokens=1000,
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                result = json.loads(resp.choices[0].message.content)
                total = float(result.get("totalScore", -1))
                if 0 <= total <= 100:
                    return {
                        "name": name,
                        "total_score": total,
                        "scores": result.get("scores", {}),
                        "reasons": result.get("scoringReasons", {}),
                    }
                logger.warning("Invalid totalScore %s for '%s' on attempt %d.", total, name, attempt + 1)
            except Exception as exc:
                logger.warning("Scoring attempt %d for '%s' failed: %s", attempt + 1, name, exc)
        return None

    ranked: List[dict] = []
    completed = 0
    total = len(candidates)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_score_one, name): name for name in candidates}
        for future in as_completed(futures):
            result = future.result()
            completed += 1
            state["progress_messages"].append(f"Scored {completed} / {total} resumes…")
            if result is not None:
                ranked.append(result)

    ranked.sort(key=lambda x: -x["total_score"])
    state["ranked_results"] = ranked[: state.get("top_n", 10)]
    return state


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _build_graph():
    g = StateGraph(RankingState)
    g.add_node("parse_jd",         parse_jd_node)
    g.add_node("skills_gap",       skills_gap_node)
    g.add_node("retrieve_resumes", retrieve_resumes_node)
    g.add_node("score_resumes",    score_resumes_node)
    g.set_entry_point("parse_jd")
    g.add_edge("parse_jd",         "skills_gap")
    g.add_edge("skills_gap",       "retrieve_resumes")
    g.add_edge("retrieve_resumes", "score_resumes")
    g.add_edge("score_resumes",    END)
    return g.compile()


_graph = None


def _get_graph():
    global _graph
    if _graph is None:
        _graph = _build_graph()
    return _graph


# ---------------------------------------------------------------------------
# Public entry point — drop-in replacement for ranking.rank_resumes()
# ---------------------------------------------------------------------------

def rank_resumes_langgraph(
    jd_text: str,
    top_n: int = 10,
    selected_resumes=None,
    on_progress=None,
) -> list:
    """
    Run the full LangGraph pipeline and return ranked results.
    Progress messages are flushed to on_progress(msg) after the graph completes.
    """
    initial_state: RankingState = {
        "jd_text": jd_text,
        "parsed_jd": None,
        "team_skill_profile": None,
        "resume_chunks": [],
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
