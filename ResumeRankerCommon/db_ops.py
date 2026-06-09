"""
All SQL Server database operations for ResumeRanker.
Uses dataclass + lru_cache for connection config; module-level singleton with reconnect on failure.
Calls stored procs for upserts; inline SQL for reads.
"""

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional

import pyodbc
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config + connection
# ---------------------------------------------------------------------------

@dataclass
class DBConfig:
    connection_string: str


@lru_cache(maxsize=1)
def get_db_config() -> DBConfig:
    conn_str = os.getenv("DB_CONNECTION_STRING", "")
    if not conn_str:
        raise ValueError("DB_CONNECTION_STRING environment variable not set")
    return DBConfig(connection_string=conn_str)


def get_db_connection() -> pyodbc.Connection:
    """
    Return a pyodbc connection. Not cached with lru_cache because connections
    can drop — callers get a fresh connection check each time via _cursor().
    """
    return pyodbc.connect(get_db_config().connection_string, autocommit=True)


# Module-level connection singleton with reconnect on failure
_conn: Optional[pyodbc.Connection] = None


def _cursor() -> pyodbc.Cursor:
    """Return a cursor, reconnecting if the current connection is stale."""
    global _conn
    try:
        if _conn is None:
            _conn = get_db_connection()
        _conn.cursor()  # lightweight check; raises if connection dropped
    except Exception:
        logger.warning("DB connection lost — reconnecting.")
        _conn = get_db_connection()
    return _conn.cursor()


# ---------------------------------------------------------------------------
# JD_Metadata operations
# ---------------------------------------------------------------------------

def upsert_jd(
    name: str,
    req_id: Optional[str] = None,
    blob_url: Optional[str] = None,
    full_text: Optional[str] = None,
) -> None:
    """Upsert a JD row. MERGE on req_id when provided, else on name."""
    cur = _cursor()
    cur.execute(
        "{CALL sp_upsert_jd (?, ?, ?, ?)}",
        (name, req_id or None, blob_url or None, full_text or None),
    )
    logger.info("Upserted JD_Metadata: name=%s req_id=%s", name, req_id)


def find_jd_by_req_id(req_id: str) -> Optional[dict]:
    """Return {name, full_text} for a JD matching req_id, or None."""
    cur = _cursor()
    cur.execute(
        "SELECT name, full_text FROM JD_Metadata WHERE req_id = ?",
        (req_id,),
    )
    row = cur.fetchone()
    if row:
        return {"name": row[0], "full_text": row[1]}
    return None


def get_jd_full_text(jd_name: str) -> Optional[str]:
    """Return the full extracted text for a JD by filename."""
    cur = _cursor()
    cur.execute(
        "SELECT full_text FROM JD_Metadata WHERE name = ?",
        (jd_name,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_jd_id(jd_name: str) -> Optional[int]:
    """Return the DB id for a JD by filename."""
    cur = _cursor()
    cur.execute("SELECT id FROM JD_Metadata WHERE name = ?", (jd_name,))
    row = cur.fetchone()
    return row[0] if row else None


def list_jd_names() -> List[str]:
    """Return all JD filenames ordered by creation date (newest first)."""
    cur = _cursor()
    cur.execute("SELECT name FROM JD_Metadata ORDER BY created_at DESC")
    return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Candidate_Profile operations
# ---------------------------------------------------------------------------

def upsert_candidate_file(
    resume_name: str,
    resume_blob_url: Optional[str] = None,
    parsed_text: Optional[str] = None,
    ai_search_doc_id: Optional[str] = None,
) -> None:
    """Store file info immediately after extraction. No GPT fields."""
    cur = _cursor()
    cur.execute(
        "{CALL sp_upsert_candidate_file (?, ?, ?, ?)}",
        (resume_name, resume_blob_url or None, parsed_text or None, ai_search_doc_id or None),
    )
    logger.info("Upserted Candidate_Profile: %s", resume_name)


def upsert_candidate_profile(
    resume_name: str,
    email: str,
    full_name: Optional[str] = None,
    phone: Optional[str] = None,
    linkedin: Optional[str] = None,
    role_family: Optional[str] = None,
    years_experience: Optional[float] = None,
    current_company: Optional[str] = None,
    latest_application_status: Optional[str] = None,
    candidate_status: Optional[str] = None,
) -> None:
    """Enrich with GPT-extracted profile fields; deduplicates by email."""
    cur = _cursor()
    cur.execute(
        "{CALL sp_upsert_candidate_profile (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}",
        (
            resume_name,
            email,
            full_name or None,
            phone or None,
            linkedin or None,
            role_family or None,
            years_experience,
            current_company or None,
            latest_application_status or None,
            candidate_status or None,
        ),
    )
    logger.info("Upserted Candidate_Profile (profile): %s email=%s", resume_name, email)


def fetch_candidate_parsed_text(resume_name: str) -> Optional[str]:
    """Return the full parsed resume text for Stage 2 scoring."""
    cur = _cursor()
    cur.execute(
        "SELECT parsed_text FROM Candidate_Profile WHERE resume_name = ?",
        (resume_name,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_candidate_id(resume_name: str) -> Optional[int]:
    """Return DB id for a candidate by resume filename."""
    cur = _cursor()
    cur.execute("SELECT id FROM Candidate_Profile WHERE resume_name = ?", (resume_name,))
    row = cur.fetchone()
    return row[0] if row else None


def list_candidate_resume_names() -> List[str]:
    """Return all indexed resume filenames ordered by creation date (newest first)."""
    cur = _cursor()
    cur.execute("SELECT resume_name FROM Candidate_Profile ORDER BY created_at DESC")
    return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Interview_History operations
# ---------------------------------------------------------------------------

def insert_interview_history(
    candidate_id: int,
    interview_date=None,
    round_type: Optional[str] = None,
    round_number: Optional[int] = None,
    result: Optional[str] = None,
    job_role: Optional[str] = None,
    req_id: Optional[str] = None,
) -> None:
    cur = _cursor()
    cur.execute(
        "{CALL sp_insert_interview_history (?, ?, ?, ?, ?, ?, ?)}",
        (candidate_id, interview_date, round_type or None, round_number, result or None, job_role or None, req_id or None),
    )
