"""
Ranking tools: GPT-4o powered candidate scoring against job descriptions.

rank_candidates_for_job — rank all (or selected) candidates against a JD.
compare_candidates       — side-by-side scoring of specific named candidates.
"""

from __future__ import annotations

from typing import List, Dict, Optional

from ResumeRankerCore.ranking import rank_resumes
from ResumeRankerCore.storage import resolve_jd_blob, fetch_blob, JD_CONTAINER
from ResumeRankerCore.text_utils import extract_text
from ResumeRankerCore.clients import get_resume_search


def _normalize(results: List[Dict]) -> List[Dict]:
    """Rename 'name' → 'candidate_name' for consistent field naming across all tools."""
    return [
        {"candidate_name": r["name"], **{k: v for k, v in r.items() if k != "name"}}
        for r in results
    ]


def rank_candidates_for_job(
    jd_identifier: str,
    top_k: int = 10,
    candidate_names: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Rank candidates against a job description using GPT-4o (6-dimension scoring).

    Use this as the primary tool when you need to find the best-fit candidates
    for a role. Examples:
    - "Top 5 candidates for JD 0001 (senior Python engineer)"
    - "Best matches for JD 0002 (data scientist)"
    - "Who fits best for JD 0003 (DevOps engineer)?"

    Two-stage pipeline for cost efficiency:
      Stage 1 — Azure hybrid search (BM25 + vector) finds top 15 candidates (free)
      Stage 2 — Parallel GPT-4o scoring of those 15 only (cost-capped regardless
                 of total corpus size)

    Scoring rubric (100 points total):
      experience      (35 pts): Years + depth of relevant work history
      technicalSkills (40 pts): Coverage of required skills, tools, languages
      certifications  ( 5 pts): Required or preferred certifications
      education       ( 5 pts): Degree, field, relevance to role
      location        ( 5 pts): Geographic fit or remote readiness
      domainFit       (10 pts): Industry/domain alignment

    Args:
        jd_identifier:   JD identifier (e.g., "0001(filename.pdf)" or blob name) — identifies the JD.
        top_k:           Number of ranked results to return (default 10).
        candidate_names: Optional list of exact resume filenames to restrict
                         ranking to a specific subset. Pass None to rank all
                         indexed candidates.

    Returns:
        List of dicts ordered best-to-worst by total_score:
          - candidate_name (str): Resume filename
          - total_score (float): 0–100 composite score
          - scores (dict): Per-dimension raw scores
              {experience, technicalSkills, certifications, education,
               location, domainFit}
          - reasons (dict): GPT-4o explanation for each dimension score

    Raises:
        ValueError: If JD not found.
    """
    blob_name = resolve_jd_blob(jd_identifier)
    jd_bytes = fetch_blob(JD_CONTAINER, blob_name)
    jd_text = extract_text(blob_name, jd_bytes)
    results = rank_resumes(
        jd_text,
        top_n=top_k,
        selected_resumes=candidate_names,
    )
    return _normalize(results)


def compare_candidates(
    jd_identifier: str,
    candidate_names: List[str],
) -> List[Dict]:
    """
    Score and rank a specific set of candidates side-by-side for a job description.

    Use this when you already know which candidates to compare — e.g.:
    - "Compare Alice Jones and John Smith for JD 0001"
    - "Score candidate_a.pdf vs candidate_b.pdf vs candidate_c.pdf for the Python role"
    - "Who is the better fit between these 3 candidates for JD 0002?"

    Unlike rank_candidates_for_job, this targets ONLY the named candidates
    (no corpus-wide search). All named candidates are scored with GPT-4o and
    returned ranked best-to-worst.

    Args:
        jd_identifier:   JD identifier (e.g., "0001(filename.pdf)" or blob name) — identifies the JD.
        candidate_names: List of 2 or more exact resume filenames (as returned
                         by list_candidates). All must exist in the system.

    Returns:
        Same structure as rank_candidates_for_job — list ordered best-to-worst.
        Each entry: {candidate_name, total_score, scores, reasons}

    Raises:
        ValueError: If fewer than 2 names are provided, JD not found,
                    or any candidate name is not found in the indexed resume corpus.
    """
    if len(candidate_names) < 2:
        raise ValueError("compare_candidates requires at least 2 candidate names.")

    indexed = set(get_resume_search().list_documents())
    missing = [n for n in candidate_names if n not in indexed]
    if missing:
        raise ValueError(
            f"The following candidates were not found in the index: {missing}. "
            "Use list_candidates() to see valid filenames."
        )

    blob_name = resolve_jd_blob(jd_identifier)
    jd_bytes = fetch_blob(JD_CONTAINER, blob_name)
    jd_text = extract_text(blob_name, jd_bytes)
    results = rank_resumes(
        jd_text,
        top_n=len(candidate_names),
        selected_resumes=candidate_names,
        # search_top scaled to corpus size so Stage 1 surfaces all named candidates
        search_top=max(25, len(candidate_names) * 3),
    )
    return _normalize(results)
