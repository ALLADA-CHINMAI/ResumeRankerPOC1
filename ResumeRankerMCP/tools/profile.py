"""
Profile tools: GPT-4o structured extraction from a candidate's resume.

get_candidate_profile — extract structured intelligence (skills, experience, certs, etc.)
analyze_skill_gaps    — compare candidate strengths/gaps against a job description
"""

from __future__ import annotations

import json
from typing import Dict

from ResumeRankerCore.clients import get_openai_client, get_resume_search, OPENAI_DEPLOYMENT
from ResumeRankerCore.storage import resolve_jd_blob, fetch_blob, JD_CONTAINER
from ResumeRankerCore.text_utils import extract_text
from ResumeRankerCore.storage import fetch_parsed_text

_PROFILE_SYSTEM = """
Extract a structured candidate profile from the resume provided. Return ONLY valid JSON with exactly these fields:
  "years_of_experience": integer — total estimated years of professional work experience
  "current_or_recent_title": string — most recent or current job title
  "key_skills": array of strings — top 10 technical and domain skills (be specific, e.g. "Python 3.x", "AWS EC2/S3", not just "programming")
  "certifications": array of strings — all certifications, licenses, and credentials mentioned (empty array if none)
  "education": string — highest degree and field, e.g. "M.S. Computer Science" or "B.E. Electrical Engineering"
  "domain_expertise": array of strings — industries or domains the candidate has worked in, e.g. ["healthcare", "fintech", "SaaS"]
  "location": string — candidate location if mentioned, otherwise "Not specified"
  "summary": string — 2-sentence professional summary highlighting the candidate's strongest qualifications

Do not include markdown, code fences, or any text outside the JSON object.
"""

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


def _get_resume_text(candidate_name: str) -> str:
    """Fetch resume text: try parsed-text cache first, fall back to index reconstruction."""
    try:
        return fetch_parsed_text(candidate_name)
    except Exception:
        return get_resume_search().get_document_text(candidate_name)


def get_candidate_profile(candidate_name: str) -> Dict:
    """
    Extract a structured intelligence profile from a candidate's resume using GPT-4o.

    Use this to quickly understand a candidate without reading the full resume, or
    to build structured comparisons across multiple candidates. Returns enterprise-
    standard candidate data: experience level, title, skills, certs, education, domain.

    Examples of when to use:
    - "What are Alice Jones's key skills and years of experience?"
    - "Give me a structured profile of candidate john_smith.pdf"
    - "What domain expertise does this candidate have?"
    - "Build profiles for these 3 candidates so I can compare them"

    Args:
        candidate_name: Exact resume filename as returned by list_candidates.
                        E.g. "john_smith.pdf" or "alice_jones.docx"

    Returns:
        Dict with:
          - candidate_name (str): The filename (echoed for reference)
          - years_of_experience (int): Total estimated years of work experience
          - current_or_recent_title (str): Most recent job title
          - key_skills (list[str]): Top 10 specific technical and domain skills
          - certifications (list[str]): All certifications mentioned (empty if none)
          - education (str): Highest degree and field
          - domain_expertise (list[str]): Industries/domains they have worked in
          - location (str): Location if mentioned, else "Not specified"
          - summary (str): 2-sentence professional summary

    Raises:
        ValueError: If candidate_name is not found in the system.
    """
    resume_text = _get_resume_text(candidate_name)
    if not resume_text or not resume_text.strip():
        raise ValueError(
            f"No resume text found for '{candidate_name}'. "
            "Use list_candidates() to verify the filename."
        )

    resp = get_openai_client().chat.completions.create(
        model=OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _PROFILE_SYSTEM},
            {"role": "user", "content": resume_text[:15000]},
        ],
        max_tokens=900,
        temperature=0,
        response_format={"type": "json_object"},
    )
    profile = json.loads(resp.choices[0].message.content)
    profile["candidate_name"] = candidate_name
    return profile


def analyze_skill_gaps(jd_identifier: str, candidate_name: str) -> Dict:
    """
    Analyze a candidate's skill gaps against a job description using GPT-4o.

    Use this to deep-dive on a specific candidate after ranking, or to build
    objective justifications for hiring decisions. Returns what the candidate
    has (strengths), what's missing (gaps with severity), and a recommendation.

    Examples of when to use:
    - "Why didn't Alice Jones score higher for JD 0001?"
    - "What skills gaps does john_smith.pdf have for JD 0003?"
    - "Give me a hire/no-hire analysis for this candidate against JD 0002"
    - "What training would candidate X need to be ready for this role?"

    Args:
        jd_identifier:  JD identifier (e.g., "0001(filename.pdf)" or blob name) — identifies the JD.
        candidate_name: Exact resume filename as returned by list_candidates.

    Returns:
        Dict with:
          - candidate_name (str): The filename (echoed for reference)
          - match_score (int): 0-100 overall fit percentage
          - strengths (list[str]): Top 3-5 things candidate has that JD requires
          - gaps (list[dict]): Missing/weak areas, each with:
              {skill, importance (critical/important/nice-to-have), notes}
          - recommendation (str): One-sentence hire/no-hire/consider with rationale

    Raises:
        ValueError: If candidate_name or requisition not found in the system.
    """
    resume_text = _get_resume_text(candidate_name)
    if not resume_text or not resume_text.strip():
        raise ValueError(
            f"No resume text found for '{candidate_name}'. "
            "Use list_candidates() to verify the filename."
        )

    blob_name = resolve_jd_blob(jd_identifier)
    jd_bytes = fetch_blob(JD_CONTAINER, blob_name)
    jd_text = extract_text(blob_name, jd_bytes)

    resp = get_openai_client().chat.completions.create(
        model=OPENAI_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _GAP_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Job Description:\n{jd_text}\n\n"
                    f"Resume:\n{resume_text[:15000]}"
                ),
            },
        ],
        max_tokens=900,
        temperature=0,
        response_format={"type": "json_object"},
    )
    result = json.loads(resp.choices[0].message.content)
    result["candidate_name"] = candidate_name
    return result
