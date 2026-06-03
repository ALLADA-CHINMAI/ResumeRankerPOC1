"""
CSV-based team skill profile reader for POC — replaces Azure SQL.

Reads a flat CSV (one row per caregiver+skill) and returns coverage fractions
with the same interface as sql_skills_client.get_team_skill_profile().

Expected CSV columns: caregiver_id, role, domain, skill_name
Set TEAM_SKILLS_CSV_PATH in .env to point at your CSV file.
Defaults to team_skills.csv in the project root if the env var is not set.
"""

import csv
import logging
import os
from collections import defaultdict
from typing import Dict, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_DEFAULT_CSV = os.path.join(os.path.dirname(__file__), "..", "team_skills.csv")
_CSV_PATH = os.getenv("TEAM_SKILLS_CSV_PATH", _DEFAULT_CSV)


def _load_csv() -> list[dict]:
    path = os.path.abspath(_CSV_PATH)
    if not os.path.exists(path):
        logger.warning("Team skills CSV not found at '%s' — gap analysis skipped.", path)
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_team_skill_profile(role: str, domain: str) -> Optional[Dict[str, float]]:
    """
    Read skill coverage for caregivers matching the given role from the CSV.

    Returns a dict mapping skill_name (lowercase) -> fraction of matching team
    members with that skill, or None if the CSV is missing or unreadable.
    """
    rows = _load_csv()
    if not rows:
        return None

    try:
        role_lower = role.lower()
        matching = [r for r in rows if role_lower in r["role"].lower()]

        if not matching:
            logger.info("No team members found for role '%s' in CSV.", role)
            return {}

        total_members = len({r["caregiver_id"] for r in matching})

        skill_holders: dict = defaultdict(set)
        for r in matching:
            skill_holders[r["skill_name"].lower()].add(r["caregiver_id"])

        return {skill: len(holders) / total_members for skill, holders in skill_holders.items()}

    except (KeyError, ZeroDivisionError) as exc:
        logger.warning("Team skill CSV parse failed (%s) — gap analysis skipped.", exc)
        return None
