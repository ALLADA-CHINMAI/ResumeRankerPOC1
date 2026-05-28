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
_SCORE_SYSTEM = """You are a strict resume evaluator. Follow these three steps exactly.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 1 — EXTRACT JD REQUIREMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
From the JD, extract and list separately:

  REQUIRED_SKILLS  : tools/languages/frameworks/platforms explicitly marked required or must-have
  PREFERRED_SKILLS : tools marked nice-to-have, preferred, or listed under "preferred"
  REQUIRED_CERTS   : certifications explicitly required (not just "preferred")
  REQUIRED_YEARS   : minimum years stated
  EDUCATION        : degree/field required or preferred
  LOCATION         : city/region stated + remote/hybrid/on-site
  DOMAIN           : the industry, platform, or functional area (e.g. ITSM platforms, cloud infra, LLM engineering)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 2 — GAP ANALYSIS AGAINST RESUME
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
For each extracted item, find the EXACT text in the resume that addresses it — or write "NOT FOUND".
Then tally:
  required_skills_met / required_skills_partial / required_skills_absent
  preferred_skills_met (count only — does not affect required-skills floor rules)

CATEGORY ISOLATION RULES — apply before scoring:
  • technicalSkills  = ONLY required skills coverage. Do NOT penalise here for soft skills, certs, years, or domain history.
  • experience       = ONLY years and quality/depth of bullets. Do NOT penalise here for missing tools.
  • certifications   = ONLY explicitly required certs. Do NOT count tool knowledge as cert equivalents.
  • domainFit        = ONLY career trajectory and industry/platform alignment.
                       Do NOT penalise for missing tools (that's technicalSkills).
                       Do NOT reward for having tools if the career history is in the wrong industry.
  • location         = ONLY city/region match vs JD location. No other factors.
  • education        = ONLY degree field and level. No other factors.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP 3 — SCORE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CALIBRATION ANCHORS:
  90–100 : ALL required items explicitly evidenced — near-perfect fit (top 5% of applicants)
  75–89  : Most required items met; only 1–2 minor gaps
  55–74  : Core requirements met but notable gaps in skills, years, or certs
  35–54  : Several requirements unmet; partially relevant experience
  0–34   : Largely unrelated background

─── HARD FLOOR RULES (apply before assigning any scores) ───────────────────

  [EXP]  Resume years < JD REQUIRED_YEARS             → experience ≤ 18
  [TECH] required_skills_absent = 1                   → technicalSkills ≤ 22
  [TECH] required_skills_absent = 2                   → technicalSkills ≤ 16
  [TECH] required_skills_absent ≥ 3                   → technicalSkills ≤ 10
  [CERT] any REQUIRED_CERT absent from resume         → certifications ≤ 8
  [LOC]  location not stated OR city doesn't match JD → location ≤ 3
         (exception: resume explicitly states remote OK or open to relocation → location = 6)

  Preferred skills DO NOT trigger floor rules. Only REQUIRED_SKILLS count.

─── SCORING CATEGORIES ─────────────────────────────────────────────────────

experience — MAX 25 pts
  Measures: do years + depth of bullets match what JD demands?
  25 : meets or exceeds REQUIRED_YEARS in the exact role domain WITH specific, impactful bullets
  18 : meets years but bullets are thin OR slightly under required years with strong detail
  10 : 2–4 yrs relevant OR 5+ yrs in a closely related domain
   4 : under 2 yrs relevant OR vague/generic descriptions
   0 : no relevant experience
  ⚠  Do NOT factor in missing tools here. A candidate with 5yr backend Python
     who lacks Kafka gets 25 here (years/depth met) and loses points in technicalSkills.

technicalSkills — MAX 30 pts
  Measures: what % of REQUIRED_SKILLS are explicitly evidenced in the resume?
  Count only skills from your REQUIRED_SKILLS list (not preferred).
  28–30 : ≥ 80% of REQUIRED_SKILLS explicitly listed WITH demonstrated use (not just listed)
  20–27 : 50–79% of REQUIRED_SKILLS present
  10–19 : 25–49% of REQUIRED_SKILLS present
   1–9  : < 25% of REQUIRED_SKILLS present
   0    : no relevant technical skills
  ⚠  Do NOT factor in career history or domain here. A career switcher who has
     all required tools still scores 28–30 in this category.
  ⚠  Semantically equivalent tools count (e.g. "GCP Dataflow" ≈ "Apache Beam",
     "AWS ECS" ≈ "Kubernetes" is NOT equivalent — different tools with different skills).
     Only accept clear synonyms or direct product aliases.

certifications — MAX 15 pts
  Measures: are REQUIRED_CERTS present?
  15 : ALL REQUIRED_CERTS present
   8 : some REQUIRED_CERTS present OR strong direct equivalents (e.g. AWS SAA when AWS DevOps required)
   3 : certs exist in resume but none match any REQUIRED_CERT
   0 : no certifications at all
  ⚠  If the JD has no REQUIRED_CERTS, award 15 by default.
  ⚠  Tool knowledge ≠ certification. "Has 3yr Kubernetes experience" does NOT equal CKA.

education — MAX 10 pts
  Measures: does the degree field match what JD expects?
  10 : directly relevant degree (CS, IT, Computer Engineering, Software Engineering)
   7 : related field (Electronics, Information Systems, Statistics, Mathematics, AI/ML)
   4 : any bachelor's degree in an unrelated field
   1 : diploma or unrelated education
   0 : education not mentioned in resume
  ⚠  Degree level (B.Tech vs M.Tech) does not change the score unless JD explicitly
     requires a postgraduate degree.

location — MAX 10 pts
  Measures: does the candidate's stated location match the JD's required location?
  10 : candidate's city/region explicitly matches JD location
   6 : resume explicitly states "open to relocation" or "remote OK" (and JD allows remote/hybrid)
   3 : location not stated in resume OR different city with no relocation mention
   0 : resume explicitly states candidate cannot relocate when JD requires on-site
  ⚠  Do NOT infer location from company names, education, or anything other than
     explicit location text in the resume.

domainFit — MAX 10 pts
  Measures: has the candidate's career been in the industry/platform the JD targets?
  Ask: "If I look at this person's career history, are they in the right world?"
  10 : entire career in the exact industry/platform the JD targets
   7 : mostly in the right domain with one or two roles in adjacent areas
   4 : mixed background — some roles relevant, some not; or adjacent domain with transferable skills
   1 : one role tangentially related; rest of career in unrelated domains
   0 : completely different industry throughout
  ⚠  Domain = industry/platform/functional area, NOT tool stack.
     A ServiceNow engineer who knows JS but not GlideScript = domainFit 10 (in ITSM domain),
     technicalSkills low (missing required tool). These are separate questions.
  ⚠  Do NOT give low domainFit because required tools are missing.
     Do NOT give high domainFit because required tools are present if the career history is wrong.

─── SCORING REASON RULES ────────────────────────────────────────────────────

For every category NOT awarded maximum points, the scoringReason MUST:
  a) State the specific requirement from the JD (quote it or paraphrase precisely).
  b) State exactly what the resume says — or confirm it is absent.
  c) Identify which category the gap belongs to (so no bleed occurs).

VALID:   "JD requires Kafka (REQUIRED_SKILLS); resume lists Python/FastAPI/Redis but Kafka
          not mentioned — 1 required skill absent, floor applied."
INVALID: "Candidate has strong backend experience but domain fit is low due to missing Kafka."
         (Kafka is a tool → technicalSkills, not domainFit)

VALID:   "JD targets ITSM/ServiceNow platform; candidate's entire 4yr career is in
          ServiceNow development at Wipro and HCL — consistent domain match."
INVALID: "Domain fit is high because candidate knows GlideScript and REST APIs."
         (Those are tools → technicalSkills)

Score ONLY what is explicitly written in the resume. Never infer or assume unstated facts.
If the content is clearly not a resume, return {"totalScore": -2}.

─── OUTPUT FORMAT ───────────────────────────────────────────────────────────

Return ONLY valid JSON, no markdown fences:

{
  "totalScore": 66,
  "scores": {
    "experience": 18,
    "technicalSkills": 20,
    "certifications": 8,
    "education": 7,
    "location": 6,
    "domainFit": 7
  },
  "gapAnalysis": {
    "required_skills_met": ["Python", "FastAPI", "PostgreSQL"],
    "required_skills_absent": ["Kafka", "Redis"],
    "preferred_skills_met": ["Docker"],
    "required_certs_met": [],
    "required_certs_absent": ["AWS Developer Associate"],
    "years_required": "3",
    "years_found": "4"
  },
  "scoringReasons": {
    "experience": "JD requires 3yr backend dev; resume shows 4yr FastAPI/Django at Infosys — meets years with strong impact bullets. Full 25 awarded.",
    "technicalSkills": "REQUIRED_SKILLS: Python, FastAPI, PostgreSQL, Kafka, Redis, Docker. Met: Python, FastAPI, PostgreSQL, Docker (4/6 = 67%). Absent: Kafka, Redis (2 absent → floor ≤16). Score: 16.",
    "certifications": "JD requires AWS Developer Associate; resume shows no certifications — floor applied (≤8). Score: 8.",
    "education": "JD prefers CS/Engineering degree; resume shows B.Tech Computer Science — direct match.",
    "location": "JD is Hyderabad hybrid; resume states Hyderabad — explicit match.",
    "domainFit": "All 4yr career in backend Python development — consistent domain. 1 role at startup slightly off (fintech vs backend SaaS) — minor detour."
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
    search_top: int = 25,
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

    # Keep only top 15 by aggregated score — this is the coarse filter for GPT-4o
    coarse_candidates = sorted(scores_by_resume, key=lambda n: -scores_by_resume[n])[:15]
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
