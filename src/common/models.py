"""
Shared data models for the ResumeRanker system.
Single source of truth for all data shapes flowing between Core, API, and MCP.
"""

from __future__ import annotations

from typing import Dict, List, Literal
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class SearchChunk(BaseModel):
    candidate_name: str
    excerpt: str
    score: float


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

class RankedCandidate(BaseModel):
    candidate_name: str
    total_score: float
    scores: Dict[str, float]
    reasons: Dict[str, str]


# ---------------------------------------------------------------------------
# Candidate profile (get_candidate_profile)
# ---------------------------------------------------------------------------

class CandidateProfile(BaseModel):
    candidate_name: str
    years_of_experience: int
    current_or_recent_title: str
    key_skills: List[str]
    certifications: List[str]
    education: str
    domain_expertise: List[str]
    location: str
    summary: str


# ---------------------------------------------------------------------------
# Skill gap analysis (analyze_skill_gaps)
# ---------------------------------------------------------------------------

class SkillGap(BaseModel):
    skill: str
    importance: Literal["critical", "important", "nice-to-have"]
    notes: str


class SkillGapAnalysis(BaseModel):
    candidate_name: str
    match_score: int
    strengths: List[str]
    gaps: List[SkillGap]
    recommendation: str
