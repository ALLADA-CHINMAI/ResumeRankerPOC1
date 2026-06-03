"""
Resume Ranking MCP Server

Tools:
  search_resumes         — hybrid semantic search via Azure AI Search
  get_candidate_history  — interview history from candidate_data.xlsx
  rerank_candidates      — Azure OpenAI reranking with interview context
"""

import json
import logging

from mcp.server.fastmcp import FastMCP

from ResumeRankerCore.clients import get_openai_client, get_resume_search, OPENAI_DEPLOYMENT
from ResumeRankerMCP.candidate_history import get_by_doc_names, get_by_candidate_ids

logger = logging.getLogger(__name__)
mcp = FastMCP("Resume Ranker MCP")


# ---------------------------------------------------------------------------
# Tool 1 — AI Search
# ---------------------------------------------------------------------------

@mcp.tool()
def search_resumes(query: str, top_k: int = 50) -> dict:
    """
    Semantic + keyword hybrid search over indexed resumes.

    Returns up to top_k candidates with their doc_name and semantic_score.
    Use doc_name values to call get_candidate_history.
    """
    try:
        chunks = get_resume_search().hybrid_search(query, top=top_k)
    except Exception as exc:
        logger.error("AI Search failed: %s", exc)
        return {"error": str(exc), "candidates": []}

    # aggregate chunk-level scores per document
    scores: dict = {}
    for chunk in chunks:
        name = chunk["doc_name"]
        scores[name] = scores.get(name, 0.0) + chunk["search_score"]

    candidates = [
        {"doc_name": name, "semantic_score": round(score, 4)}
        for name, score in sorted(scores.items(), key=lambda x: -x[1])
    ]
    return {"candidates": candidates}


# ---------------------------------------------------------------------------
# Tool 2 — Candidate History
# ---------------------------------------------------------------------------

@mcp.tool()
def get_candidate_history(
    doc_names: list[str] | None = None,
    candidate_ids: list[str] | None = None,
) -> dict:
    """
    Read candidate metadata and full interview history from candidate_data.xlsx.

    Pass doc_names (from search_resumes) OR candidate_ids — not both required.
    Returns each candidate's profile plus every interview round on record.
    """
    if not doc_names and not candidate_ids:
        return {"error": "Provide doc_names or candidate_ids", "candidates": []}
    try:
        if doc_names:
            results = get_by_doc_names(doc_names)
        else:
            results = get_by_candidate_ids(candidate_ids)
        return {"candidates": results}
    except FileNotFoundError as exc:
        return {"error": str(exc), "candidates": []}
    except Exception as exc:
        logger.error("Candidate history lookup failed: %s", exc)
        return {"error": str(exc), "candidates": []}


# ---------------------------------------------------------------------------
# Tool 3 — Rerank
# ---------------------------------------------------------------------------

_RERANK_SYSTEM = """
You are an expert technical recruiter. Rank the provided candidates for the given job description.

For each candidate consider:
- Semantic match score from search
- Years of experience and role fit
- Past interview history: how far they got, rejection reasons, interview scores
- Recency of their last application

Return valid JSON: {"ranked_candidates": [{"candidate_id": "...", "full_name": "...", "score": <0-100>, "explanation": "..."}]}
Order by score descending. No markdown, no extra text.
"""


@mcp.tool()
def rerank_candidates(jd: str, candidates: list[dict]) -> dict:
    """
    Use Azure OpenAI to produce a final ranked list.

    candidates should be the merged output of search_resumes + get_candidate_history
    (join on doc_name / latest_ai_search_doc_id).
    """
    if not candidates:
        return {"ranked_candidates": []}

    candidates_json = json.dumps(candidates, indent=2)
    try:
        resp = get_openai_client().chat.completions.create(
            model=OPENAI_DEPLOYMENT,
            messages=[
                {"role": "system", "content": _RERANK_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Job Description:\n{jd}\n\n"
                        f"Candidates:\n{candidates_json}"
                    ),
                },
            ],
            max_tokens=2000,
            temperature=0,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)
        return result
    except Exception as exc:
        logger.error("Rerank failed: %s", exc)
        return {"error": str(exc), "ranked_candidates": []}


if __name__ == "__main__":
    mcp.run()
