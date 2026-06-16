"""
REST API for ResumeRanker — exposes ranking functionality to external applications.

Run locally:
    uvicorn ResumeRankerMCP.api.main:app --host 0.0.0.0 --port 8000

Endpoints:
    POST /rankResumes  — rank existing indexed resumes against a JD identified by req_id
"""

from typing import List, Optional
import json
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ResumeRankerMCP.common.models import RankedCandidate
from ResumeRankerMCP.common.ranking import rank_resumes
from ResumeRankerMCP.common.storage import JD_CONTAINER, RESUME_CONTAINER, get_blob_url
from ResumeRankerMCP.common.clients import get_blob_service

app = FastAPI(title="ResumeRanker API", version="1.0.0")
KEYWORD_CONTAINER = os.getenv("JD_KEYWORDS_CONTAINER_NAME", "jds-keywords")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class RankRequest(BaseModel):
    req_id: Optional[str] = None
    top_k: int = 10
    natural_language_query: Optional[str] = None


class CandidateResult(RankedCandidate):
    rank: int
    resume_blob_url: str


class RankResponse(BaseModel):
    req_id: str
    jd_name: str
    jd_blob_url: str
    query_text: str
    jd_keywords: dict
    results: List[CandidateResult]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _load_jd_payload(req_id: str) -> dict:
    if not req_id:
        return {}
    blob_client = get_blob_service().get_blob_client(KEYWORD_CONTAINER, f"{req_id}.json")
    if not blob_client.exists():
        return {}
    payload = json.loads(blob_client.download_blob().readall().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _build_search_query_from_keywords(jd_keywords: dict, jd_text: str) -> str:
    if not isinstance(jd_keywords, dict):
        return jd_text
    parts: list[str] = []
    for _k, v in jd_keywords.items():
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
        elif isinstance(v, list):
            items = [str(item).strip() for item in v if str(item).strip()]
            if items:
                parts.append(" ".join(items))
    query = " ".join(parts).strip()
    return query or jd_text


def _find_jd_blob_by_req_id(req_id: str) -> str:
    if not req_id:
        return ""
    container_client = get_blob_service().get_container_client(JD_CONTAINER)
    matches: list[str] = []
    for blob in container_client.list_blobs(name_starts_with=req_id):
        name = getattr(blob, "name", "") or ""
        stem = os.path.splitext(os.path.basename(name))[0]
        if stem == req_id:
            matches.append(name)

    if matches:
        matches.sort(key=lambda x: (len(x), x.lower()))
        return matches[0]
    return ""


@app.post("/rankResumes", response_model=RankResponse)
def rank(request: RankRequest):
    jd_payload = _load_jd_payload(request.req_id) if request.req_id else {}

    if jd_payload:
        jd_text = (jd_payload.get("jd_text") or "").strip()
        if not jd_text.strip():
            raise HTTPException(
                status_code=500,
                detail=(
                    f"JD payload for req_id '{request.req_id}' has no jd_text. "
                    "Re-upload JD so trigger can persist jd_text into keyword JSON."
                ),
            )

        jd_keywords = jd_payload.get("keywords", {}) if isinstance(jd_payload.get("keywords", {}), dict) else {}
        jd_blob_name = _find_jd_blob_by_req_id(request.req_id or "")
        jd_name = (jd_payload.get("jd_name") or jd_blob_name or f"{request.req_id}.txt").strip()
        search_query_text = (
            request.natural_language_query.strip()
            if request.natural_language_query and request.natural_language_query.strip()
            else _build_search_query_from_keywords(jd_keywords, jd_text)
        )
    else:
        if not request.natural_language_query or not request.natural_language_query.strip():
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No JD keyword payload found for req_id '{request.req_id}'. "
                    "Provide natural_language_query to rank without a stored JD payload."
                ),
            )
        jd_text = request.natural_language_query.strip()
        jd_keywords = {}
        jd_blob_name = ""
        jd_name = request.req_id or "natural-language-query"
        search_query_text = request.natural_language_query.strip()

    raw_results = rank_resumes(
        jd_text,
        top_n=request.top_k,
        search_query_text=search_query_text,
    )

    results = []
    for i, r in enumerate(raw_results):
        resume_name = r["candidate_name"]
        results.append(
            CandidateResult(
                rank=i + 1,
                resume_blob_url=get_blob_url(RESUME_CONTAINER, resume_name),
                **r,
            )
        )

    return RankResponse(
        req_id=request.req_id or "",
        jd_name=jd_name,
        jd_blob_url=get_blob_url(JD_CONTAINER, jd_blob_name) if jd_blob_name else "",
        query_text=search_query_text,
        jd_keywords=jd_keywords,
        results=results,
    )
