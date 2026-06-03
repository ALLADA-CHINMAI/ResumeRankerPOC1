"""LangGraph state models for the multi-agent ranking pipeline."""

from typing import Dict, List, Optional
from typing_extensions import TypedDict


class ParsedJD(TypedDict):
    role: str
    required_skills: List[str]
    nice_to_have_skills: List[str]
    experience_level: str   # junior | mid | senior
    domain: str
    certifications: List[str]
    location: str


class TeamSkillProfile(TypedDict):
    skill_coverage: Dict[str, float]   # skill -> fraction of team that has it
    gap_skills: List[str]              # required skills where team coverage < 50%
    skill_weights: Dict[str, float]    # skill -> scoring weight multiplier


class ResumeResult(TypedDict):
    name: str
    total_score: float
    scores: Dict[str, float]
    reasons: Dict[str, str]


class RankingState(TypedDict):
    jd_text: str
    parsed_jd: Optional[ParsedJD]
    team_skill_profile: Optional[TeamSkillProfile]
    resume_chunks: List[Dict]
    selected_resumes: Optional[List[str]]
    top_n: int
    ranked_results: List[ResumeResult]
    progress_messages: List[str]
