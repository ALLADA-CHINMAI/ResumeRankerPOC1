"""
Reads candidate_data.xlsx (two sheets) and returns structured candidate + interview history.

Sheet 1 — candidate_metadata : candidate identity and lifecycle state
Sheet 2 — interview_history  : per-round interview records

Resolution: doc_name (from AI Search) → latest_ai_search_doc_id → candidate_id → history rows
"""

import logging
import os
from typing import Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "..", "candidate_data.xlsx")
_EXCEL_PATH = os.getenv("CANDIDATE_DATA_PATH", _DEFAULT_PATH)


def _load() -> Tuple[pd.DataFrame, pd.DataFrame]:
    path = os.path.abspath(_EXCEL_PATH)
    if not os.path.exists(path):
        raise FileNotFoundError(f"candidate_data.xlsx not found at: {path}")
    meta = pd.read_excel(path, sheet_name="candidate_metadata", dtype=str).fillna("")
    hist = pd.read_excel(path, sheet_name="interview_history", dtype=str).fillna("")
    return meta, hist


def get_by_doc_names(doc_names: List[str]) -> List[Dict]:
    """
    Resolve doc_names (AI Search doc keys) → candidate_ids, return merged candidate dicts.
    Each dict contains candidate metadata + list of interview_history rows.
    """
    meta, hist = _load()

    # resolve doc_name → candidate_id via latest_ai_search_doc_id column
    matched = meta[meta["latest_ai_search_doc_id"].isin(doc_names)]

    return _build_results(matched, hist)


def get_by_candidate_ids(candidate_ids: List[str]) -> List[Dict]:
    """Return merged candidate dicts for the given candidate_ids."""
    meta, hist = _load()
    matched = meta[meta["candidate_id"].isin(candidate_ids)]
    return _build_results(matched, hist)


def _build_results(matched: pd.DataFrame, hist: pd.DataFrame) -> List[Dict]:
    results = []
    for _, row in matched.iterrows():
        cid = row["candidate_id"]
        history_rows = hist[hist["candidate_id"] == cid].to_dict(orient="records")
        results.append({
            "candidate_id": cid,
            "full_name": row.get("full_name", ""),
            "primary_email": row.get("primary_email", ""),
            "role_family": row.get("role_family", ""),
            "years_experience": row.get("years_experience", ""),
            "current_company": row.get("current_company", ""),
            "latest_application_status": row.get("latest_application_status", ""),
            "last_interview_round": row.get("last_interview_round", ""),
            "total_applications": row.get("total_applications", ""),
            "candidate_status": row.get("candidate_status", ""),
            "latest_resume_blob_url": row.get("latest_resume_blob_url", ""),
            "latest_ai_search_doc_id": row.get("latest_ai_search_doc_id", ""),
            "interview_history": history_rows,
        })
    return results
