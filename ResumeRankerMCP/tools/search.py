"""
Search tool: fast hybrid BM25 + vector search over indexed resumes.
No GPT-4o calls — use this for exploration before committing to ranking.
"""

from __future__ import annotations

from typing import List, Dict, Optional

from ResumeRankerCore.clients import get_resume_search
from ResumeRankerCore.storage import fetch_blob, fetch_parsed_text, JD_CONTAINER
from ResumeRankerCore.text_utils import extract_text


def search_candidates(
    query: str,
    top_k: int = 10,
    candidate_names: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Search resumes using hybrid BM25 + vector similarity (Azure Cognitive Search).

    Use this for natural language queries like:
    - "Python developer with AWS and Kubernetes experience"
    - "5+ years software engineering in healthcare or fintech"
    - "PMP certified project manager with Agile experience"
    - "Machine learning engineer with PyTorch and TensorFlow"
    - "Senior data scientist SQL Python 3+ years"

    This is the fast, free exploration tool — no GPT-4o calls.
    Ideal for quick candidate discovery before a full ranking run.
    Each result is one matching chunk (~400 tokens); the same resume may
    appear multiple times if multiple sections match strongly.

    Args:
        query:           Natural language or keyword search query.
        top_k:           Number of matching chunks to return (default 10).
                         Increase to 20-30 for broader exploration.
        candidate_names: Optional list of exact resume filenames to restrict
                         the search to a specific subset of candidates.

    Returns:
        List of dicts ordered by relevance score descending:
          - candidate_name (str): Resume filename the chunk belongs to
          - excerpt (str): The matching text excerpt (~400 tokens)
          - score (float): Azure Cognitive Search hybrid relevance score
    """
    chunks = get_resume_search().hybrid_search(
        query, top=top_k, filter_names=candidate_names
    )
    return [
        {
            "candidate_name": c["doc_name"],
            "excerpt": c["chunk_text"],
            "score": round(c["search_score"], 4),
        }
        for c in chunks
    ]


def get_jd_text(jd_name: str) -> str:
    """
    Retrieve the full text of a stored job description by filename.

    Use this to fetch a JD from the system so you can pass its text to
    rank_candidates_for_job, compare_candidates, or analyze_skill_gaps
    without requiring the user to paste the JD manually.

    Args:
        jd_name: Exact JD filename as returned by list_job_descriptions.
                 E.g. "senior_software_engineer_jd.pdf"

    Returns:
        Full extracted plaintext of the job description.

    Raises:
        ValueError: If jd_name is not found in blob storage.
    """
    try:
        raw = fetch_blob(JD_CONTAINER, jd_name)
    except Exception as e:
        raise ValueError(f"JD '{jd_name}' not found in storage: {e}") from e
    return extract_text(jd_name, raw)
