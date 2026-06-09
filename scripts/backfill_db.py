"""
Backfill existing blob/xlsx data into the new SQL tables.

Migrates:
  1. JDs        — 'jds' blob container → JD_Metadata (name, blob_url, full_text)
  2. Resumes    — 'resumes' + 'resumes-parsed' blobs → Candidate_Profile (resume_name, parsed_text, blob_url)
  3. Interviews — candidate_data.xlsx (interview_history sheet) → Interview_History

Run once after deploying code and before removing old data paths:
    python scripts/backfill_db.py

Requires .env with DB_CONNECTION_STRING, AZURE_STORAGE_CONNECTION_STRING set.
"""

import logging
import os
import sys

from dotenv import load_dotenv

# Allow importing from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def backfill_jds():
    """Migrate JD blobs to JD_Metadata table."""
    from ResumeRankerCommon.storage import list_blobs, fetch_blob, JD_CONTAINER
    from ResumeRankerCommon.text_utils import extract_text
    from ResumeRankerCommon.db_ops import upsert_jd
    from ResumeRankerCommon.clients import STORAGE_CONN_STR

    account_name = ""
    for part in (STORAGE_CONN_STR or "").split(";"):
        if part.startswith("AccountName="):
            account_name = part.split("=", 1)[1]
            break

    jd_names = list_blobs(JD_CONTAINER)
    logger.info("Found %d JDs in blob container '%s'.", len(jd_names), JD_CONTAINER)

    ok = fail = 0
    for name in jd_names:
        try:
            raw = fetch_blob(JD_CONTAINER, name)
            text = extract_text(name, raw)
            blob_url = f"https://{account_name}.blob.core.windows.net/{JD_CONTAINER}/{name}"
            upsert_jd(name=name, blob_url=blob_url, full_text=text or None)
            logger.info("  JD upserted: %s (%d chars)", name, len(text))
            ok += 1
        except Exception as e:
            logger.error("  JD FAILED: %s — %s", name, e)
            fail += 1

    logger.info("JDs done: %d ok, %d failed.", ok, fail)


def backfill_resumes():
    """Migrate resume blobs + resumes-parsed text to Candidate_Profile table."""
    from ResumeRankerCommon.storage import list_blobs, fetch_blob, RESUME_CONTAINER, PARSED_TEXT_CONTAINER
    from ResumeRankerCommon.text_utils import extract_text
    from ResumeRankerCommon.db_ops import upsert_candidate_file
    from ResumeRankerCommon.clients import STORAGE_CONN_STR

    account_name = ""
    for part in (STORAGE_CONN_STR or "").split(";"):
        if part.startswith("AccountName="):
            account_name = part.split("=", 1)[1]
            break

    resume_names = list_blobs(RESUME_CONTAINER)
    logger.info("Found %d resumes in blob container '%s'.", len(resume_names), RESUME_CONTAINER)

    # Build a set of names that already have parsed text cached in resumes-parsed
    try:
        parsed_names = set(list_blobs(PARSED_TEXT_CONTAINER))
    except Exception:
        parsed_names = set()
    logger.info("Found %d pre-parsed text blobs.", len(parsed_names))

    ok = fail = 0
    for name in resume_names:
        try:
            blob_url = f"https://{account_name}.blob.core.windows.net/{RESUME_CONTAINER}/{name}"

            # Try to get pre-parsed text from resumes-parsed container first (fast)
            text = None
            if name in parsed_names:
                try:
                    raw = fetch_blob(PARSED_TEXT_CONTAINER, name)
                    text = raw.decode("utf-8")
                except Exception:
                    pass

            # Fall back to extracting from original blob
            if not text:
                raw = fetch_blob(RESUME_CONTAINER, name)
                text = extract_text(name, raw)

            upsert_candidate_file(
                resume_name=name,
                resume_blob_url=blob_url,
                parsed_text=text or None,
                ai_search_doc_id=name,
            )
            logger.info("  Resume upserted: %s (%d chars)", name, len(text) if text else 0)
            ok += 1
        except Exception as e:
            logger.error("  Resume FAILED: %s — %s", name, e)
            fail += 1

    logger.info("Resumes done: %d ok, %d failed.", ok, fail)


def backfill_interviews():
    """Migrate interview_history sheet from candidate_data.xlsx to Interview_History table."""
    import pandas as pd
    from ResumeRankerCommon.db_ops import get_candidate_id, insert_interview_history

    xlsx_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "candidate_data.xlsx")
    if not os.path.exists(xlsx_path):
        logger.warning("candidate_data.xlsx not found at %s — skipping interview backfill.", xlsx_path)
        return

    try:
        df = pd.read_excel(xlsx_path, sheet_name="interview_history")
    except Exception as e:
        logger.warning("Could not read interview_history sheet: %s", e)
        return

    logger.info("Found %d interview rows in candidate_data.xlsx.", len(df))

    # Expected columns (adjust names to match your actual xlsx headers)
    col_map = {
        "resume_name": ["resume_name", "filename", "file_name", "Resume Name"],
        "interview_date": ["interview_date", "date", "Interview Date"],
        "round_type": ["round_type", "type", "Round Type"],
        "round_number": ["round_number", "round", "Round Number"],
        "result": ["result", "outcome", "Result"],
        "job_role": ["job_role", "role", "Job Role"],
        "req_id": ["req_id", "Req ID", "requisition_id"],
    }

    def find_col(df, candidates):
        for c in candidates:
            if c in df.columns:
                return c
        return None

    resume_col = find_col(df, col_map["resume_name"])
    if not resume_col:
        logger.error("Cannot find resume_name column in interview_history sheet. Columns: %s", list(df.columns))
        return

    ok = fail = skip = 0
    for _, row in df.iterrows():
        resume_name = str(row.get(resume_col, "")).strip()
        if not resume_name:
            skip += 1
            continue

        candidate_id = get_candidate_id(resume_name)
        if not candidate_id:
            logger.warning("  No Candidate_Profile row for '%s' — skipping interview row.", resume_name)
            skip += 1
            continue

        def _val(candidates):
            col = find_col(df, candidates)
            if col is None:
                return None
            v = row.get(col)
            if v is None or (isinstance(v, float) and str(v) == "nan"):
                return None
            return v

        try:
            interview_date = _val(col_map["interview_date"])
            if hasattr(interview_date, "date"):
                interview_date = interview_date.date()
            insert_interview_history(
                candidate_id=candidate_id,
                interview_date=interview_date,
                round_type=_val(col_map["round_type"]),
                round_number=_val(col_map["round_number"]),
                result=_val(col_map["result"]),
                job_role=_val(col_map["job_role"]),
                req_id=_val(col_map["req_id"]),
            )
            ok += 1
        except Exception as e:
            logger.error("  Interview FAILED for '%s': %s", resume_name, e)
            fail += 1

    logger.info("Interviews done: %d ok, %d skipped, %d failed.", ok, skip, fail)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Backfill existing blob/xlsx data into SQL tables.")
    parser.add_argument("--jds", action="store_true", help="Backfill JD_Metadata from 'jds' blob container")
    parser.add_argument("--resumes", action="store_true", help="Backfill Candidate_Profile from 'resumes' blob container")
    parser.add_argument("--interviews", action="store_true", help="Backfill Interview_History from candidate_data.xlsx")
    parser.add_argument("--all", action="store_true", help="Run all three backfills in order")
    args = parser.parse_args()

    if not any([args.jds, args.resumes, args.interviews, args.all]):
        parser.print_help()
        sys.exit(1)

    if args.all or args.jds:
        logger.info("=== Backfilling JDs ===")
        backfill_jds()

    if args.all or args.resumes:
        logger.info("=== Backfilling Resumes ===")
        backfill_resumes()

    if args.all or args.interviews:
        logger.info("=== Backfilling Interviews ===")
        backfill_interviews()

    logger.info("Backfill complete.")
