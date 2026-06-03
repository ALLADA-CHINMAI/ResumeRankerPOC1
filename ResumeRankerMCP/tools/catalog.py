"""
Catalog tool: discover what candidates and job descriptions are in the system.
Lightweight — no LLM calls, no search scoring, just index/blob enumeration.
"""

from __future__ import annotations

from typing import List

from ResumeRankerCore.clients import get_resume_search
from ResumeRankerCore.storage import list_blobs, JD_CONTAINER


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


def list_job_descriptions() -> List[str]:
    """
    Return all job description filenames stored in the system.

    Use this to discover what JDs are available. JD filenames can be passed
    to get_jd_text to retrieve their content for use in ranking tools.

    Returns:
        List of JD filenames from blob storage.
        Example: ["senior_swe_jd.pdf", "devops_engineer_jd.txt"]
    """
    return list_blobs(JD_CONTAINER)
