"""
Search tool: fast hybrid BM25 + vector search over indexed resumes.
No GPT-4o calls — use this for exploration before committing to ranking.
"""

from __future__ import annotations

from typing import List, Dict, Optional

from ResumeRankerMCP.common.clients import get_resume_search
from ResumeRankerMCP.common.storage import fetch_blob, fetch_parsed_text, JD_CONTAINER
from ResumeRankerMCP.common.text_utils import extract_text


def search_candidates(
    query: str,
    top_k: int = 10,
    candidate_names: Optional[List[str]] = None,
) -> List[Dict]:
    """
    Hybrid BM25+vector search over resumes. No LLM calls — fast exploration before ranking.
    Returns chunks: {candidate_name, excerpt, score}. Same resume may appear multiple times.

    Args:
        query: Natural language or keyword search query.
        top_k: Number of chunks to return (default 10).
        candidate_names: Optional filenames to restrict search scope.
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
    Fetch full plaintext of a stored job description.

    Args:
        jd_name: Exact JD filename as returned by list_job_descriptions.
    """
    try:
        raw = fetch_blob(JD_CONTAINER, jd_name)
    except Exception as e:
        raise ValueError(f"JD '{jd_name}' not found in storage: {e}") from e
    return extract_text(jd_name, raw)
