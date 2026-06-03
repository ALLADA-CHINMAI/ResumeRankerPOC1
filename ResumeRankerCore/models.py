"""State models for the Resume Ranking pipeline."""

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


class InterviewRound(TypedDict):
    interview_id: str
    job_role: str
    round_number: str
    round_type: str
    result: str
    rejection_reason: str
    interview_score: str
    interview_feedback: str
    interview_date: str


class CandidateProfile(TypedDict):
    candidate_id: str
    full_name: str
    primary_email: str
    role_family: str
    years_experience: str
    current_company: str
    latest_application_status: str
    last_interview_round: str
    total_applications: str
    candidate_status: str
    latest_resume_blob_url: str
    latest_ai_search_doc_id: str
    semantic_score: float
    interview_history: List[InterviewRound]


class RankedCandidate(TypedDict):
    candidate_id: str
    full_name: str
    score: float
    explanation: str
    resume_blob_url: str


class RankingState(TypedDict):
    jd_text: str
    parsed_jd: Optional[ParsedJD]
    resume_candidates: List[Dict]          # raw search results {doc_name, semantic_score}
    candidate_profiles: List[CandidateProfile]  # merged search + history
    selected_resumes: Optional[List[str]]
    top_n: int
    ranked_results: List[RankedCandidate]
    progress_messages: List[str]
