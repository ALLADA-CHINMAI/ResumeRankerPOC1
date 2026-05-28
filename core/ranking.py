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

from core.clients import get_openai_client, get_resume_search, OPENAI_DEPLOYMENT
from core.storage import fetch_parsed_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring rubric (prompt + category caps)
# ---------------------------------------------------------------------------

_SCORE_SYSTEM = """You are a strict resume evaluator. Score each resume against the job description using the rubrics below.

CALIBRATION — realistic distribution across a candidate pool:
  90–100 : exceptional fit; meets EVERY listed requirement with explicit, detailed evidence (rare — top 5% of applicants)
  75–89  : strong fit; meets most requirements; only 1–2 clearly minor gaps
  55–74  : moderate fit; meets core requirements but has notable gaps in skills, years, or certifications
  35–54  : weak fit; missing multiple requirements; only partially relevant experience
  0–34   : poor fit; largely unrelated role, domain, or skill set

DIFFERENTIATION RULE:
- Scores across a pool MUST be meaningfully spread. Most candidates fall in the 55–80 range.
- Do NOT cluster candidates within 5 points of each other unless their resumes are genuinely near-identical.
- Reserve 90+ strictly for candidates who satisfy every requirement with clear written evidence.
- A candidate missing even one required certification, skill, or year of experience CANNOT score 90+.

Scoring categories — scores MUST exactly sum to totalScore; NEVER exceed the listed maximum:

  experience — MAX 25 pts
    25 : meets or exceeds required years in the exact platform/domain with rich, relevant bullet points
    18 : meets years but bullets are thin, OR slightly under required years with strong detail
    10 : 2–4 yrs relevant experience, or 5+ yrs in a related but different domain
     4 : under 2 yrs relevant, or experience is vaguely described
     0 : no relevant experience

  technicalSkills — MAX 30 pts
    28–30 : explicitly lists ≥80% of required skills/tools with demonstrated use
    20–27 : lists 50–79% of required skills
    10–19 : lists 25–49% of required skills
     1–9  : lists <25% of required skills
     0    : no relevant technical skills

  certifications — MAX 15 pts
    15 : all certifications explicitly required by the JD are present
     8 : some but not all required certifications; or equivalent certifications
     3 : certifications exist but none match what the JD requires
     0 : no certifications at all

  education — MAX 10 pts
    10 : degree in a directly relevant field (CS, IT, Engineering)
     7 : degree in a related field
     4 : any bachelor's degree
     1 : no degree or unrelated education
     0 : education not mentioned

  location — MAX 10 pts
    10 : location explicitly matches the JD location
     6 : states open to relocation or remote
     3 : location mentioned but does not match; or location not stated
     0 : explicitly states cannot relocate when JD requires it

  domainFit — MAX 10 pts
    10 : entire career is in the exact industry/platform the JD targets
     7 : mostly in the right domain with minor detours
     4 : partially in the domain; mixed background
     1 : adjacent domain with transferable skills
     0 : completely different industry or domain

Rules:
- HARD CAP: A category score may NEVER exceed its listed maximum. 11/10 or 16/15 are invalid and will be rejected.
- Score ONLY on what is explicitly written in the resume. Never infer or assume.
- For every category where the candidate did NOT receive the maximum, the scoringReason MUST state specifically what was missing or insufficient (e.g. "JD requires 5 yrs AWS Lambda; resume shows 3 yrs general cloud").
- Only count semantically equivalent terms for clearly synonymous titles/tools (e.g. "ML engineer" ≈ "machine learning developer"). Do NOT stretch equivalence.
- Deduct heavily when the JD lists a specific required certification and the resume does not have it.
- If the content is clearly not a resume, return {"totalScore": -2}.

Return ONLY valid JSON, no markdown fences:
{
  "totalScore": 66.0,
  "scores": {
    "experience": 18,
    "technicalSkills": 20,
    "certifications": 8,
    "education": 7,
    "location": 6,
    "domainFit": 7
  },
  "scoringReasons": {
    "experience": "Lists 4 years in cloud but JD requires 6 years with specific AWS Lambda expertise; Lambda not mentioned",
    "technicalSkills": "Has Python, SQL, Docker — missing Kubernetes and Terraform which the JD explicitly requires",
    "certifications": "Holds AWS Solutions Architect but JD also requires Azure Administrator; Azure cert absent",
    "education": "BS in Information Systems — related but not a core CS/Engineering degree",
    "location": "Resume shows Chicago; JD requires Austin TX with no remote option stated",
    "domainFit": "Fintech background is relevant but 2 of 5 roles are in unrelated retail domain"
  }
}"""

# Maximum points per category — used for validation and UI display
SCORE_MAX = {
    "experience": 25,
    "technicalSkills": 30,
    "certifications": 15,
    "education": 10,
    "location": 10,
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
    search_top: int = 100,
    on_progress: Optional[callable] = None,
) -> List[dict]:
    """
    Rank resumes against a job description.

    Two-stage pipeline:
      Stage 1 — hybrid search + search-score aggregation → top 25 candidates (fast, free)
      Stage 2 — ThreadPoolExecutor with up to 8 parallel GPT-4o scoring calls (capped at 25)

    GPT-4o calls are always capped at ~25 regardless of how many resumes are in the corpus,
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

    # Keep only top 25 by aggregated score — this is the coarse filter
    coarse_candidates = sorted(scores_by_resume, key=lambda n: -scores_by_resume[n])[:25]
    _p(f"Found {len(scores_by_resume)} candidates — scoring top {len(coarse_candidates)} with GPT-4o…")
    logger.info(
        "Coarse filter: %d unique resumes found → top %d forwarded to GPT-4o scoring.",
        len(scores_by_resume),
        len(coarse_candidates),
    )

    # Stage 2 — parallel GPT-4o scoring of top 25
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
