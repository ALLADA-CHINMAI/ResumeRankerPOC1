"""
Catalog tool: discover what candidates and job descriptions are in the system.
Lightweight — no LLM calls, no search scoring, just index/blob enumeration.
"""

from __future__ import annotations

from typing import List

from ResumeRankerMCP.common.clients import get_resume_search
from ResumeRankerMCP.common.storage import list_blobs, JD_CONTAINER


def list_candidates() -> List[str]:
    """Return all resume filenames indexed in the system."""
    return get_resume_search().list_documents()


def list_job_descriptions() -> List[str]:
    """Return all job description filenames in blob storage."""
    return list_blobs(JD_CONTAINER)
