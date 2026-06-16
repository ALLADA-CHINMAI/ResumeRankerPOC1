from __future__ import annotations

from typing import List, Optional

from ResumeRankerMCP.common.models import RankedCandidate
from ResumeRankerMCP.common.ranking import rank_resumes
from ResumeRankerMCP.common.clients import get_resume_search


def rank_candidates_for_job(
    jd_text: str,
    top_k: int = 10,
    candidate_names: Optional[List[str]] = None,
) -> List[RankedCandidate]:
    """
    Rank candidates against a JD using GPT-4o (6 dimensions, 100 pts total).
    Stage 1: hybrid search narrows to top 15. Stage 2: parallel GPT-4o scoring.
    Returns [{candidate_name, total_score, scores, reasons}] ordered best-to-worst.

    Args:
        jd_text: Full job description text.
        top_k: Number of results to return (default 10).
        candidate_names: Restrict to specific filenames; None = all candidates.
    """
    return rank_resumes(
        jd_text,
        top_n=top_k,
        selected_resumes=candidate_names,
    )


def compare_candidates(
    jd_text: str,
    candidate_names: List[str],
) -> List[RankedCandidate]:
    """
    Score 2+ named candidates side-by-side with GPT-4o for a role.
    All named candidates are scored; returns same structure as rank_candidates_for_job.

    Args:
        jd_text: Full job description text.
        candidate_names: 2+ exact resume filenames from list_candidates.
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

    return rank_resumes(
        jd_text,
        top_n=len(candidate_names),
        selected_resumes=candidate_names,
        search_top=max(25, len(candidate_names) * 3),
    )
