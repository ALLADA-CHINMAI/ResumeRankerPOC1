"""
Catalog tool: discover what candidates and job descriptions are in the system.
Lightweight — no LLM calls, no search scoring, just index/blob enumeration.
"""

from __future__ import annotations

from typing import List, Dict

from ResumeRankerCore.clients import get_resume_search
from ResumeRankerCore.db import list_jds as db_list_jds


def list_candidates() -> List[str]:
    """
    Return all resume filenames currently indexed in the system.

    Use this first to discover what candidates are available before calling
    search_candidates, rank_candidates_for_job, or compare_candidates.
    Results are resume filenames exactly as they were uploaded (e.g. "john_smith.pdf").

    Returns:
        List of resume filenames. Empty list if no resumes are indexed yet.
        Example: ["john_smith.pdf", "alice_jones.docx", "bob_kumar.txt"]
    """
    return get_resume_search().list_documents()


def list_job_descriptions() -> List[Dict]:
    """
    Return all job descriptions stored in the system with their req IDs.

    Use this to discover available JDs. Pass the req_id to get_jd_text
    to retrieve its content for use in ranking and analysis tools.

    Returns:
        List of dicts ordered by req_id ascending.
        Example: [{"req_id": "0001", "jd_name": "senior_swe_jd.pdf"}, ...]
    """
    return db_list_jds()
