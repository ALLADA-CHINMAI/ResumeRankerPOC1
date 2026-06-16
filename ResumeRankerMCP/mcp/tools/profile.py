"""
Profile tools: GPT-4o structured extraction from a candidate's resume.

get_candidate_profile — extract structured intelligence (skills, experience, certs, etc.)
analyze_skill_gaps    — compare candidate strengths/gaps against a job description
"""

from __future__ import annotations

import json
from typing import Dict

from ResumeRankerMCP.common.clients import get_openai_client, get_resume_search, OPENAI_DEPLOYMENT
from ResumeRankerMCP.common.models import CandidateProfile, SkillGapAnalysis
from ResumeRankerMCP.common.storage import fetch_parsed_text

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
    Extract structured profile from a resume using GPT-4o.
    Returns: years_of_experience, current_or_recent_title, key_skills, certifications,
             education, domain_expertise, location, summary.

    Args:
        candidate_name: Exact resume filename from list_candidates.
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
    raw = json.loads(resp.choices[0].message.content)
    return CandidateProfile(candidate_name=candidate_name, **raw).model_dump()


def analyze_skill_gaps(jd_text: str, candidate_name: str) -> Dict:
    """
    Compare a candidate's resume against a JD using GPT-4o.
    Returns: match_score (0-100), strengths, gaps (skill/importance/notes), recommendation.

    Args:
        jd_text: Full job description text.
        candidate_name: Exact resume filename from list_candidates.
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
    raw = json.loads(resp.choices[0].message.content)
    return SkillGapAnalysis(candidate_name=candidate_name, **raw).model_dump()
