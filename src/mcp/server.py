from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv
from fastmcp import FastMCP

from src.common.clients import (
    OPENAI_DEPLOYMENT,
    get_blob_service,
    get_openai_client,
    get_resume_search,
    validate_config,
)
from src.common.models import SkillGapAnalysis
from src.common.ranking import rank_resumes
from src.common.storage import fetch_parsed_text
from src.mcp.server_config import configure_logging

KEYWORD_CONTAINER = os.getenv("JD_KEYWORDS_CONTAINER_NAME", "jds-keywords")

_GAP_SYSTEM = """
You are an expert talent analyst. Compare the candidate's resume against the job description provided.
Return ONLY valid JSON with exactly these fields:
  "match_score": integer 0-100 — overall candidate-to-job fit percentage
  "strengths": array of strings — top 3-5 specific things the candidate has that the JD requires
  "gaps": array of objects — missing or weak areas, each with:
      "skill": string — the specific skill, requirement, or quality that is lacking
      "importance": string — one of: "critical", "important", "nice-to-have"
      "notes": string — brief explanation of the gap and its impact
  "recommendation": string — one-sentence hire/no-hire/consider recommendation with concise rationale

Do not include markdown, code fences, or any text outside the JSON object.
"""


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


def _resolve_jd_text(req_id: str) -> tuple[str, dict]:
    payload = _load_jd_payload(req_id)
    if not payload:
        raise ValueError(
            f"No JD keyword payload found for req_id '{req_id}'. Upload JD as '{req_id}.<ext>' first."
        )
    jd_text = (payload.get("jd_text") or "").strip()
    if not jd_text:
        raise ValueError(
            f"JD payload for req_id '{req_id}' has no jd_text. Re-upload JD so trigger persists jd_text."
        )
    return jd_text, payload


def _get_resume_text(candidate_name: str) -> str:
    try:
        return fetch_parsed_text(candidate_name)
    except Exception:
        return get_resume_search().get_document_text(candidate_name)


mcp = FastMCP(
    name="ResumeRanker",
    version="2.0.0",
    instructions=(
        "Three tools only:\n"
        "1. rankresumes_by_req_id\n"
        "2. rankresumes_by_search_query\n"
        "3. skill_gap_analysis"
    ),
)


@mcp.tool()
def rankresumes_by_req_id(req_id: str, top_k: int = 10, natural_language_query: str = "") -> dict:
    """Rank resumes using JD payload identified by req_id. Optional natural_language_query overrides stage-1 search text."""
    jd_text, payload = _resolve_jd_text((req_id or "").strip())
    jd_keywords = payload.get("keywords", {}) if isinstance(payload.get("keywords", {}), dict) else {}
    search_query_text = (
        natural_language_query.strip()
        if natural_language_query and natural_language_query.strip()
        else _build_search_query_from_keywords(jd_keywords, jd_text)
    )
    results = rank_resumes(jd_text, top_n=top_k, search_query_text=search_query_text)
    return {
        "req_id": req_id,
        "query_text": search_query_text,
        "jd_keywords": jd_keywords,
        "results": results,
    }


@mcp.tool()
def rankresumes_by_search_query(search_query: str, top_k: int = 10) -> dict:
    """Rank resumes directly from a natural-language search query (no req_id/JD payload required)."""
    query = (search_query or "").strip()
    if not query:
        raise ValueError("search_query is required.")
    results = rank_resumes(query, top_n=top_k, search_query_text=query)
    return {"query_text": query, "results": results}


@mcp.tool()
def skill_gap_analysis(candidate_name: str, jd_text: str = "", req_id: str = "", search_query: str = "") -> dict:
    """Analyze candidate skill gaps using jd_text directly, or via req_id payload, or via search_query."""
    candidate = (candidate_name or "").strip()
    if not candidate:
        raise ValueError("candidate_name is required.")

    if jd_text and jd_text.strip():
        effective_jd_text = jd_text.strip()
    elif req_id and req_id.strip():
        effective_jd_text, _ = _resolve_jd_text(req_id.strip())
    elif search_query and search_query.strip():
        effective_jd_text = search_query.strip()
    else:
        raise ValueError("Provide one of jd_text, req_id, or search_query.")

    resume_text = _get_resume_text(candidate)
    if not resume_text or not resume_text.strip():
        raise ValueError(f"No resume text found for '{candidate}'.")

    resp = get_openai_client().chat.completions.create(
        model=OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _GAP_SYSTEM},
            {
                "role": "user",
                "content": f"Job Description:\n{effective_jd_text}\n\nResume:\n{resume_text[:15000]}",
            },
        ],
        max_tokens=900,
        temperature=0,
        response_format={"type": "json_object"},
    )
    raw = json.loads(resp.choices[0].message.content)
    return SkillGapAnalysis(candidate_name=candidate, **raw).model_dump()


def main() -> None:
    parser = argparse.ArgumentParser(description="ResumeRanker MCP server.")
    parser.add_argument("--transport", choices=["stdio", "http", "sse"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    load_dotenv()
    configure_logging()

    try:
        validate_config()
    except ValueError as e:
        logging.critical("Azure configuration error:\n%s", e)
        sys.exit(1)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="sse", host=args.host, port=args.port)


if __name__ == "__main__":
    main()