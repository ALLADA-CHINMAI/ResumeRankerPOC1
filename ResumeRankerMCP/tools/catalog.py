"""
Catalog tool: discover what candidates and job descriptions are in the system.
Lightweight — no LLM calls, no search scoring, just DB/blob enumeration.
"""

from __future__ import annotations

from typing import List

from ResumeRankerCommon.clients import get_resume_search
from ResumeRankerCommon.storage import list_blobs, JD_CONTAINER


def list_candidates() -> List[str]:
    """Return all resume filenames indexed in the system."""
    try:
        from ResumeRankerCommon.db_ops import list_candidate_resume_names
        names = list_candidate_resume_names()
        if names:
            return names
    except Exception:
        pass
    return get_resume_search().list_documents()


def list_job_descriptions() -> List[str]:
    """Return all job description filenames in the system."""
    try:
        from ResumeRankerCommon.db_ops import list_jd_names
        names = list_jd_names()
        if names:
            return names
    except Exception:
        pass
    return list_blobs(JD_CONTAINER)
