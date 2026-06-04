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

from ResumeRankerCore.clients import get_openai_client, get_resume_search, OPENAI_DEPLOYMENT
from ResumeRankerCore.storage import fetch_parsed_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring rubric (prompt + category caps)
# ---------------------------------------------------------------------------


_SCORE_SYSTEM = """
You are an expert resume evaluator. For each resume, score the following categories, using only the information in the job description and the resume:

experience (max 35): Total years and depth of relevant work experience for the job, including how well the candidate's roles and responsibilities match the job description.
technicalSkills (max 40): Coverage and proficiency in the specific technical skills, tools, languages, or platforms required by the job description.
certifications (max 5): If the job description explicitly requires or prefers certifications/licenses, score based on how well the candidate meets them. If the job description does NOT mention any certifications or licenses, award the full 5 points automatically.
education (max 5): If the job description explicitly requires a specific degree, field, or education level, score based on how well the candidate meets it. If the job description does NOT mention any education requirements, award the full 5 points automatically.
location (max 5): If the job description explicitly specifies a location, on-site requirement, or remote policy, score based on how well the candidate matches it. If the job description does NOT mention any location requirements, award the full 5 points automatically.
domainFit (max 10): Alignment of the candidate's career history and industry/domain experience with the target job's field or sector, not location.

For each category, assign a score from 0 up to the max. Also provide a brief reason for each score. Return valid JSON with totalScore (sum of all categories), a 'scores' object, and a 'scoringReasons' object. In 'scoringReasons' object clearly provide why the score was assigned and why was it reduced if its less than the maximum score and at the end of reason mention if that's category is a full/partial/no match with the job description. Do not include markdown or extra text.
For each entry in 'scoringReasons': begin with exactly one of these status words followed by a colon — 'Exceeds:', 'Aligned:', 'Partial:', or 'Didn\'t Meet:'. After the colon, describe what matched or exceeded the requirements. If any requirements were not satisfied, append a sentence starting with 'Not Met:' followed by what was missing or insufficient.
"""

# Maximum points per category — used for validation and UI display
SCORE_MAX = {
    "experience": 35,
    "technicalSkills": 40,
    "certifications": 5,
    "education": 5,
    "location": 5,
    "domainFit": 10,
}


def _scores_valid(result: dict) -> bool:
    """Ensure every category is within its cap and the sum matches totalScore."""
    scores = result.get("scores", {})
    for key, cap in SCORE_MAX.items():
        val = scores.get(key)
        if val is None or not (0 <= float(val) <= cap):
            logger.warning("Invalid score for '%s': %s (max %s)", key, val, cap)
            return False
    computed = sum(float(scores[k]) for k in SCORE_MAX)
    total = float(result.get("totalScore", -1))
    if abs(total - computed) > 1:
        logger.warning("totalScore %s does not match category sum %s", total, computed)
        return False
    return True


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


@lru_cache(maxsize=64)
def extract_jd_keywords_structured(jd_text: str) -> dict:
    """
    Extract category-wise keywords from a JD as a structured dict.
    Cached separately from extract_jd_keywords so both can co-exist.
    """
    resp = get_openai_client().chat.completions.create(
        model=OPENAI_DEPLOYMENT,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract key requirements from a job description and return JSON with exactly these keys:\n"
                    '{"jobTitle": "string", '
                    '"technicalSkills": ["short item", ...], '
                    '"experience": "string", '
                    '"certifications": ["short item", ...], '
                    '"education": "string", '
                    '"location": "string", '
                    '"domain": "string"}\n'
                    "Keep each list item short (1–4 words). Omit keys with no relevant information."
                ),
            },
            {"role": "user", "content": jd_text},
        ],
        max_tokens=600,
        temperature=0,
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def score_resume(jd_text: str, resume_text: str, retries: int = 3) -> Optional[dict]:
    """Score a single resume against a JD. Returns None if content is not a valid resume."""
    for attempt in range(retries):
        try:
            resp = get_openai_client().chat.completions.create(
                model=OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": _SCORE_SYSTEM},
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
            total = float(result.get("totalScore", -1))
            if total == -2:
                return None  # content is not a resume
            if 0 <= total <= 100 and _scores_valid(result):
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
    on_progress: Optional[callable] = None,
) -> List[dict]:
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
        on_progress:      Optional callback(message: str) for real-time UI progress updates.
    """
    _p = on_progress or (lambda msg: None)  # no-op if no callback provided
    resume_search = get_resume_search()

    # Stage 1 — keyword extraction + hybrid search
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
        return {
            "name": name,
            "total_score": result["totalScore"],
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
