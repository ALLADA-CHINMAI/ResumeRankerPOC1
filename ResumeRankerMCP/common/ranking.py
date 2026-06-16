"""
Resume ranking pipeline — pure business logic, zero Streamlit imports.
Safe to call from the Streamlit UI, a CLI, or a future MCP server.

Two-stage approach for scale:
  Stage 1 (free):  hybrid search → aggregate scores per resume → keep top 25
  Stage 2 (paid):  parallel GPT-4o scoring of top 25 only (capped regardless of corpus size)
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import List, Optional

from ResumeRankerMCP.common.clients import get_openai_client, get_resume_search, OPENAI_DEPLOYMENT
from ResumeRankerMCP.common.config import get_score_system, get_score_max
from ResumeRankerMCP.common.models import RankedCandidate
from ResumeRankerMCP.common.storage import fetch_parsed_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring configuration (loaded from scoring_config.json)
# ---------------------------------------------------------------------------

# Cache SCORE_MAX at module load for quick access (loaded once on first use)
_SCORE_MAX_CACHE = None


def _get_score_max_cached() -> dict:
    """Get cached SCORE_MAX dict from config."""
    global _SCORE_MAX_CACHE
    if _SCORE_MAX_CACHE is None:
        _SCORE_MAX_CACHE = get_score_max()
    return _SCORE_MAX_CACHE


def _scores_valid(result: dict) -> bool:
    """Ensure every category is within its cap."""
    score_max = _get_score_max_cached()
    scores = result.get("scores", {})
    for key, cap in score_max.items():
        val = scores.get(key)
        if val is None or not (0 <= float(val) <= cap):
            logger.warning("Invalid score for '%s': %s (max %s)", key, val, cap)
            return False
    return True


def _compute_total_score(result: dict) -> float:
    """Compute total score from the validated category scores."""
    score_max = _get_score_max_cached()
    scores = result.get("scores", {})
    return sum(float(scores[k]) for k in score_max)


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=64)
def extract_jd_keywords(jd_text: str) -> str:
    """
    Extract dense keyword text from a JD for hybrid search.
    Cached (lru_cache) — the same JD text won't trigger a second LLM call.
    """
    resp = get_openai_client().chat.completions.create(
        model=OPENAI_DEPLOYMENT,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract key requirements from a job description for semantic resume search. "
                    "Output dense continuous text (no bullets, no headers) covering: "
                    "job title, required skills, technologies, years of experience, "
                    "certifications, education level, and industry domain."
                ),
            },
            {"role": "user", "content": jd_text},
        ],
        max_tokens=600,
        temperature=0,
    )
    return resp.choices[0].message.content


def score_resume(jd_text: str, resume_text: str, retries: int = 3) -> Optional[dict]:
    """Score a single resume against a JD. Returns None if content is not a valid resume."""
    score_system = get_score_system()
    for attempt in range(retries):
        try:
            resp = get_openai_client().chat.completions.create(
                model=OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": score_system},
                    {
                        "role": "user",
                        "content": (
                            f"Job Description:\n{jd_text}\n\n"
                            f"Resume:\n{resume_text[:15000]}"
                        ),
                    },
                ],
                max_tokens=1000,
                temperature=0,
                response_format={"type": "json_object"},
            )
            result = json.loads(resp.choices[0].message.content)
            model_total = float(result.get("totalScore", -1))
            if model_total == -2:
                return None  # content is not a resume
            if _scores_valid(result):
                return result
            logger.warning("Score attempt %d returned invalid result, retrying.", attempt + 1)
        except Exception as e:
            logger.warning("Score attempt %d failed: %s", attempt + 1, e)
    return None


# ---------------------------------------------------------------------------
# Main ranking pipeline
# ---------------------------------------------------------------------------

def rank_resumes(
    jd_text: str,
    top_n: int = 10,
    selected_resumes: Optional[List[str]] = None,
    search_top: int = 25,
    search_query_text: Optional[str] = None,
    on_progress: Optional[callable] = None,
) -> List[RankedCandidate]:
    """
    Rank resumes against a job description.

    Two-stage pipeline:
      Stage 1 — hybrid search + search-score aggregation → top 25 candidates (fast, free)
      Stage 2 — ThreadPoolExecutor with up to 8 parallel GPT-4o scoring calls (capped at 15)

    GPT-4o calls are always capped at ~15 regardless of how many resumes are in the corpus,
    so this stays fast at 1000+ resumes.

    Args:
        jd_text:          Full text of the job description.
        top_n:            Number of results to return (default 10).
        selected_resumes: If provided, only rank these resumes (multiselect support).
        search_top:       How many chunks to retrieve in Stage 1. Increase for larger corpora.
        search_query_text: Precomputed keyword/query text for Stage 1 hybrid search.
        on_progress:      Optional callback(message: str) for real-time UI progress updates.
    """
    _p = on_progress or (lambda msg: None)  # no-op if no callback provided
    resume_search = get_resume_search()

    # Stage 1 — keyword extraction + hybrid search
    if search_query_text and search_query_text.strip():
        keywords = search_query_text.strip()
        _p("Using precomputed JD keywords…")
        logger.info("Using precomputed JD keyword text for hybrid search.")
    else:
        _p("Extracting JD keywords…")
        logger.info("Extracting JD keywords (cached if seen before)...")
        keywords = extract_jd_keywords(jd_text)

    _p(f"Searching indexed resumes (top {search_top} chunks)…")
    logger.info("Hybrid search (top=%d, filter=%s)...", search_top, bool(selected_resumes))
    chunks = resume_search.hybrid_search(
        keywords,
        top=search_top,
        filter_names=selected_resumes if selected_resumes else None,
    )

    if not chunks:
        logger.warning("Hybrid search returned no results.")
        _p("No matching resumes found in the index.")
        return []

    # Aggregate search scores per resume to identify the strongest candidates
    scores_by_resume: dict = {}
    for chunk in chunks:
        name = chunk["doc_name"]
        scores_by_resume[name] = scores_by_resume.get(name, 0.0) + chunk["search_score"]

    # Keep only top 15 by aggregated score — this is the coarse filter for GPT-4o
    coarse_candidates = sorted(scores_by_resume, key=lambda n: -scores_by_resume[n])[:15]
    _p(f"Found {len(scores_by_resume)} candidates — scoring top {len(coarse_candidates)} with GPT-4o…")
    logger.info(
        "Coarse filter: %d unique resumes found → top %d forwarded to GPT-4o scoring.",
        len(scores_by_resume),
        len(coarse_candidates),
    )

    # Stage 2 — parallel GPT-4o scoring of top 15
    def _score_one(name: str) -> Optional[dict]:
        try:
            # Prefer the cached parsed-text blob (fast blob read, no index query needed)
            resume_text = fetch_parsed_text(name)
        except Exception:
            # Fallback: reconstruct from search index chunks (for resumes uploaded before this refactor)
            logger.warning("Parsed-text blob missing for '%s', falling back to index reconstruction.", name)
            resume_text = resume_search.get_document_text(name)

        result = score_resume(jd_text, resume_text)
        if result is None:
            return None
        total_score = _compute_total_score(result)
        return {
            "candidate_name": name,
            "total_score": total_score,
            "scores": result.get("scores", {}),
            "reasons": result.get("scoringReasons", {}),
        }

    ranked: List[dict] = []
    completed = 0
    total = len(coarse_candidates)
    # max_workers=8: balances Azure OpenAI TPM limits with parallelism gains
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_score_one, name): name for name in coarse_candidates}
        for future in as_completed(futures):
            result = future.result()
            completed += 1
            _p(f"Scored {completed} / {total} resumes…")
            if result is not None:
                ranked.append(result)

    ranked.sort(key=lambda x: -x["total_score"])
    logger.info("Scoring complete. Returning top %d of %d.", top_n, len(ranked))
    return ranked[:top_n]
