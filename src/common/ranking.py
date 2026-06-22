"""
Resume ranking pipeline — pure business logic, zero Streamlit imports.
Safe to call from the Streamlit UI, a CLI, or a future MCP server.

Two-stage approach for scale:
    Stage 1 (free):  hybrid search → aggregate scores per resume
    Stage 2 (paid):  batched GPT-4o scoring (5 resumes per call, capped regardless of corpus size)
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from typing import List, Optional

from src.common.clients import get_openai_client, get_resume_search, OPENAI_DEPLOYMENT
from src.common.config import get_score_system, get_score_max
from src.common.models import RankedCandidate
from src.common.storage import fetch_parsed_text

logger = logging.getLogger(__name__)
BATCH_SCORE_SIZE = 5

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


def score_resumes_batch(
    jd_text: str,
    resumes: List[dict],
    retries: int = 3,
) -> List[dict]:
    """Score multiple resumes in one GPT call. Expects up to BATCH_SCORE_SIZE resumes."""
    if not resumes:
        return []

    score_system = get_score_system()
    payload = [
        {
            "candidate_name": r["candidate_name"],
            "resume_text": r["resume_text"][:15000],
        }
        for r in resumes
    ]

    system_prompt = (
        f"{score_system}\n\n"
        "You will receive multiple resumes in one request. "
        "Return ONLY valid JSON with this exact shape:\n"
        "{\n"
        '  "results": [\n'
        "    {\n"
        '      "candidate_name": "string",\n'
        '      "totalScore": number,\n'
        '      "scores": {...},\n'
        '      "scoringReasons": {...}\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Include exactly one result per input resume. Use the same candidate_name values as input."
    )

    for attempt in range(retries):
        try:
            resp = get_openai_client().chat.completions.create(
                model=OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Job Description:\n{jd_text}\n\n"
                            f"Resumes JSON:\n{json.dumps(payload, ensure_ascii=True)}"
                        ),
                    },
                ],
                max_tokens=3000,
                temperature=0,
                response_format={"type": "json_object"},
            )

            parsed = json.loads(resp.choices[0].message.content)
            raw_results = parsed.get("results", []) if isinstance(parsed, dict) else []
            if not isinstance(raw_results, list):
                logger.warning("Batch score response has non-list 'results'.")
                continue

            by_name = {
                str(item.get("candidate_name", "")): item
                for item in raw_results
                if isinstance(item, dict)
            }

            final_results: List[dict] = []
            for resume in resumes:
                name = resume["candidate_name"]
                item = by_name.get(name)
                if not item:
                    logger.warning("Batch score missing candidate '%s'.", name)
                    continue

                model_total = float(item.get("totalScore", -1))
                if model_total == -2:
                    continue  # content is not a resume
                if not _scores_valid(item):
                    logger.warning("Invalid category scores for '%s' in batch response.", name)
                    continue

                final_results.append(
                    {
                        "candidate_name": name,
                        "total_score": _compute_total_score(item),
                        "scores": item.get("scores", {}),
                        "reasons": item.get("scoringReasons", {}),
                    }
                )

            return final_results
        except Exception as e:
            logger.warning("Batch scoring attempt %d failed: %s", attempt + 1, e)

    return []


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
            Stage 1 — hybrid search + search-score aggregation (fast, free)
            Stage 2 — batched GPT-4o scoring of top 15 candidates (5 resumes per call)

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

    # Stage 2 — batched GPT-4o scoring (5 resumes per call)
    resume_payloads: List[dict] = []
    for name in coarse_candidates:
        try:
            # Prefer the cached parsed-text blob (fast blob read, no index query needed)
            resume_text = fetch_parsed_text(name)
        except Exception:
            # Fallback: reconstruct from search index chunks (for resumes uploaded before this refactor)
            logger.warning("Parsed-text blob missing for '%s', falling back to index reconstruction.", name)
            resume_text = resume_search.get_document_text(name)
        resume_payloads.append({"candidate_name": name, "resume_text": resume_text})

    batches = [
        resume_payloads[i: i + BATCH_SCORE_SIZE]
        for i in range(0, len(resume_payloads), BATCH_SCORE_SIZE)
    ]

    ranked: List[dict] = []
    completed = 0
    total = len(resume_payloads)
    # Parallelize by batch to keep call count low and throughput high.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(score_resumes_batch, jd_text, batch): batch for batch in batches}
        for future in as_completed(futures):
            batch = futures[future]
            batch_results = future.result() or []
            completed += len(batch)
            _p(f"Scored {completed} / {total} resumes…")
            ranked.extend(batch_results)

    ranked.sort(key=lambda x: -x["total_score"])
    logger.info("Scoring complete. Returning top %d of %d.", top_n, len(ranked))
    return ranked[:top_n]
