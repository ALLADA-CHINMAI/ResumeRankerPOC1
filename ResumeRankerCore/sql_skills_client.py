"""
Azure SQL connector for team skill coverage queries.
Returns None gracefully when AZURE_SQL_CONNECTION_STRING is not set or query fails,
so the LangGraph pipeline can degrade gracefully without SQL.
"""

import os
import logging
from typing import Dict, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_CONN_STR = os.getenv("AZURE_SQL_CONNECTION_STRING")


def get_team_skill_profile(role: str, domain: str) -> Optional[Dict[str, float]]:
    """
    Query skill coverage for caregivers in a given role from Azure SQL.

    Returns a dict mapping skill_name (lowercase) -> fraction of team members with that skill,
    or None if the SQL connection is unavailable or the query fails.

    Expected schema:
        caregiver(id, role, domain, ...)
        caregiver_skills(caregiver_id, skill_id)
        skills(id, skill_name)
    """
    if not _CONN_STR:
        return None

    try:
        import pyodbc  # optional dependency
        conn = pyodbc.connect(_CONN_STR, timeout=10)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                s.skill_name,
                CAST(COUNT(DISTINCT cs.caregiver_id) AS FLOAT) /
                    NULLIF((SELECT COUNT(*) FROM caregiver WHERE role LIKE ?), 0) AS coverage
            FROM caregiver c
            JOIN caregiver_skills cs ON c.id = cs.caregiver_id
            JOIN skills s ON cs.skill_id = s.id
            WHERE c.role LIKE ?
            GROUP BY s.skill_name
            """,
            (f"%{role}%", f"%{role}%"),
        )
        rows = cursor.fetchall()
        conn.close()

        return {row.skill_name.lower(): float(row.coverage) for row in rows}

    except Exception as exc:
        logger.warning("Team skill query failed (%s) — gap analysis skipped.", exc)
        return None
