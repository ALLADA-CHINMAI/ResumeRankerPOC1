"""
REST API for ResumeRanker — exposes ranking functionality to external applications.

Run locally:
    uvicorn ResumeRankerAPI.main:app --host 0.0.0.0 --port 8000

Endpoints:
    POST /rankResumes  — rank existing indexed resumes against a JD identified by req_id
"""

from typing import Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ResumeRankerCommon.db_ops import find_jd_by_req_id, get_jd_full_text
from ResumeRankerCommon.models import RankedCandidate
from ResumeRankerCommon.ranking import extract_jd_keywords_structured, rank_resumes

app = FastAPI(title="ResumeRanker API", version="1.0.0")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RankRequest(BaseModel):
    req_id: str
    top_k: int = 10


class CandidateResult(RankedCandidate):
    rank: int


class RankResponse(BaseModel):
    req_id: str
    jd_name: str
    jd_keywords: dict
    results: List[CandidateResult]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/rankResumes", response_model=RankResponse)
def rank(request: RankRequest):
    # Single DB query returns both name and full_text
    jd = find_jd_by_req_id(request.req_id)
    if not jd:
        # Fallback: blob metadata scan for pre-migration JDs
        from ResumeRankerCommon.storage import find_jd_by_req_id as storage_find
        jd = storage_find(request.req_id)
        if not jd:
            raise HTTPException(
                status_code=404,
                detail=f"No JD found for req_id '{request.req_id}'",
            )

    jd_name = jd["name"]
    jd_text = jd.get("full_text")

    # full_text may be None for pre-migration JDs found via blob fallback
    if not jd_text:
        jd_text = get_jd_full_text(jd_name)
    if not jd_text or not jd_text.strip():
        raise HTTPException(
            status_code=500,
            detail=f"JD '{jd_name}' has no retrievable text.",
        )

    try:
        jd_keywords = extract_jd_keywords_structured(jd_text)
    except Exception:
        jd_keywords = {}

    raw_results = rank_resumes(jd_text, top_n=request.top_k, jd_name=jd_name)

    results = [
        CandidateResult(rank=i + 1, **r)
        for i, r in enumerate(raw_results)
    ]

    return RankResponse(
        req_id=request.req_id,
        jd_name=jd_name,
        jd_keywords=jd_keywords,
        results=results,
    )
