"""
Azure Functions for ResumeRanker.

2 functions:
  process_new_resume — BlobTrigger: extract text, index to Search, upsert Candidate_Profile
  process_new_jd     — BlobTrigger: extract text, upsert JD_Metadata (no Search indexing)

Both functions are zero-GPT — processing happens synchronously on blob arrival.
Ranking is done on-demand via the UI / API (two-stage: hybrid search + live GPT-4o).
"""

import logging
import os
import sys

import azure.functions as func

# Make ResumeRankerCommon importable from the parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ResumeRankerCommon.text_utils import extract_text
from ResumeRankerCommon.clients import get_resume_search, STORAGE_CONN_STR
from ResumeRankerCommon.db_ops import upsert_candidate_file, upsert_jd

logger = logging.getLogger(__name__)

app = func.FunctionApp()

# Storage account name parsed once from connection string
_ACCOUNT_NAME = ""
for _part in (STORAGE_CONN_STR or "").split(";"):
    if _part.startswith("AccountName="):
        _ACCOUNT_NAME = _part.split("=", 1)[1]
        break


def _blob_url(container: str, name: str) -> str:
    return f"https://{_ACCOUNT_NAME}.blob.core.windows.net/{container}/{name}"


# ---------------------------------------------------------------------------
# BlobTrigger: resume arrives
# Zero GPT — extract, index, store. Ranking happens on-demand.
# ---------------------------------------------------------------------------

@app.blob_trigger(
    arg_name="blob",
    path="resumes/{name}",
    connection="AzureWebJobsStorage",
)
def process_new_resume(blob: func.InputStream, name: str):
    logger.info("process_new_resume triggered: %s (%d bytes)", name, blob.length)
    data = blob.read()

    text = extract_text(name, data)
    if not text.strip():
        logger.warning("No text extracted from resume '%s' — skipping.", name)
        return

    # Index into resume_chunks so Stage 1 hybrid search can find this resume
    get_resume_search().index_document(name, text)

    blob_url = _blob_url("resumes", name)
    upsert_candidate_file(
        resume_name=name,
        resume_blob_url=blob_url,
        parsed_text=text,
        ai_search_doc_id=name,
    )
    logger.info("Candidate_Profile upserted: %s", name)


# ---------------------------------------------------------------------------
# BlobTrigger: JD arrives
# Zero GPT — extract and store full_text in DB. No Search indexing needed.
# ---------------------------------------------------------------------------

@app.blob_trigger(
    arg_name="blob",
    path="jds/{name}",
    connection="AzureWebJobsStorage",
)
def process_new_jd(blob: func.InputStream, name: str):
    logger.info("process_new_jd triggered: %s (%d bytes)", name, blob.length)
    data = blob.read()

    text = extract_text(name, data)
    if not text.strip():
        logger.warning("No text extracted from JD '%s' — skipping.", name)
        return

    req_id = None
    if blob.metadata:
        req_id = blob.metadata.get("req_id")

    blob_url = _blob_url("jds", name)
    upsert_jd(name=name, req_id=req_id, blob_url=blob_url, full_text=text)
    logger.info("JD_Metadata upserted: %s req_id=%s", name, req_id)
