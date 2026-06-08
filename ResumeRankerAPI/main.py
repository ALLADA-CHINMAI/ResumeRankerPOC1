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

from ResumeRankerCommon.clients import get_jd_search
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
    jd_search = get_jd_search()

    jd_name = jd_search.find_doc_by_req_id(request.req_id)
    if not jd_name:
        raise HTTPException(
            status_code=404,
            detail=f"No JD found for req_id '{request.req_id}'",
        )

    jd_text = jd_search.get_document_text(jd_name)
    if not jd_text.strip():
        raise HTTPException(
            status_code=500,
            detail=f"JD '{jd_name}' is indexed but has no retrievable text.",
        )

    try:
        jd_keywords = extract_jd_keywords_structured(jd_text)
    except Exception:
        jd_keywords = {}

    raw_results = rank_resumes(jd_text, top_n=request.top_k)

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
