"""
Azure Blob Storage helpers.
Also manages the 'resumes-parsed' container that caches extracted resume text
so ranking doesn't need to reassemble it from search index chunks.
"""

import os
import logging
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import List

from dotenv import load_dotenv
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from ResumeRankerCommon.clients import get_blob_service

load_dotenv()
logger = logging.getLogger(__name__)

# Blob container names from environment
RESUME_CONTAINER      = os.getenv("RESUME_CONTAINER_NAME", "resumes")
JD_CONTAINER          = os.getenv("JD_CONTAINER_NAME", "jds")
PARSED_TEXT_CONTAINER = "resumes-parsed"   # auto-created; stores plain-text cache for ranking

# Track which containers we've already ensured exist in this process
_ensured_containers: set = set()


def _ensure_container(name: str):
    """Create blob container if it doesn't exist. Cached per process to avoid repeated API calls."""
    if name in _ensured_containers:
        return
    try:
        get_blob_service().create_container(name)
        logger.info("Created blob container '%s'.", name)
    except Exception:
        pass  # ResourceExistsError is expected and safe to ignore
    _ensured_containers.add(name)


# ---------------------------------------------------------------------------
# Core blob helpers
# ---------------------------------------------------------------------------

def list_blobs(container: str) -> List[str]:
    """List all blob names in a container. Returns empty list if container is empty."""
    return [b.name for b in get_blob_service().get_container_client(container).list_blobs()]


def fetch_blob(container: str, name: str) -> bytes:
    """Download and return blob contents as bytes."""
    return (
        get_blob_service()
        .get_blob_client(container=container, blob=name)
        .download_blob()
        .readall()
    )


def upload_blob(container: str, name: str, data: bytes, metadata: dict = None):
    """Upload bytes to a blob, overwriting any existing content."""
    get_blob_service().get_blob_client(container=container, blob=name).upload_blob(
        data, overwrite=True, metadata=metadata
    )


def find_jd_by_req_id(req_id: str):
    """Return {name, full_text} for a JD matching req_id, or None.
    Reads from DB first; falls back to blob metadata scan for pre-migration JDs.
    """
    try:
        from ResumeRankerCommon.db_ops import find_jd_by_req_id as db_find
        result = db_find(req_id)
        if result:
            return result
    except Exception as e:
        logger.warning("DB find_jd_by_req_id failed, falling back to blob scan: %s", e)

    # Fallback: blob metadata scan (pre-migration JDs that aren't in DB yet)
    container_client = get_blob_service().get_container_client(JD_CONTAINER)
    for blob in container_client.list_blobs(include=["metadata"]):
        if blob.get("metadata") and blob["metadata"].get("req_id") == req_id:
            return {"name": blob.name, "full_text": None}
    return None


# ---------------------------------------------------------------------------
# Parsed-text cache
# Stored in 'resumes-parsed' container using the original filename as the blob key.
# Avoids re-assembling resume text from search index chunks during every ranking run.
# ---------------------------------------------------------------------------

def store_parsed_text(doc_name: str, text: str):
    """Cache extracted resume text. Writes to DB (primary) and blob (kept as fallback)."""
    try:
        from ResumeRankerCommon.db_ops import upsert_candidate_file
        upsert_candidate_file(resume_name=doc_name, parsed_text=text)
    except Exception as e:
        logger.warning("DB store_parsed_text failed, writing blob only: %s", e)
    # Keep blob write during transition so pre-Function uploads still have a fallback
    _ensure_container(PARSED_TEXT_CONTAINER)
    upload_blob(PARSED_TEXT_CONTAINER, doc_name, text.encode("utf-8"))


def fetch_parsed_text(doc_name: str) -> str:
    """Fetch cached resume text. Reads from DB first; falls back to blob for pre-migration data."""
    try:
        from ResumeRankerCommon.db_ops import fetch_candidate_parsed_text
        text = fetch_candidate_parsed_text(doc_name)
        if text:
            return text
    except Exception as e:
        logger.warning("DB fetch_parsed_text failed, falling back to blob: %s", e)

    # Fallback: resumes-parsed blob (pre-migration resumes or DB miss)
    data = fetch_blob(PARSED_TEXT_CONTAINER, doc_name)
    return data.decode("utf-8")


_INLINE_TYPES = {
    ".pdf":  "application/pdf",
    ".txt":  "text/plain",
}
_OFFICE_EXTS = {".docx", ".doc"}


def get_blob_url(container: str, name: str, expiry_minutes: int = 60) -> str:
    """Return a URL that opens the blob for viewing (not downloading) in a browser tab.

    - PDF / TXT  → direct SAS URL with inline Content-Disposition (browser renders natively).
    - DOCX / DOC → Microsoft Office Online viewer wrapping the SAS URL (no local install needed).
    - Other      → plain SAS URL (browser default behaviour).
    """
    from ResumeRankerCommon.clients import STORAGE_CONN_STR
    parts = {}
    for segment in STORAGE_CONN_STR.split(";"):
        if "=" in segment:
            k, _, v = segment.partition("=")
            parts[k] = v
    account_name   = parts.get("AccountName", "")
    account_key    = parts.get("AccountKey", "")
    endpoint_suffix = parts.get("EndpointSuffix", "core.windows.net")

    ext = os.path.splitext(name)[1].lower()
    content_type = _INLINE_TYPES.get(ext)

    sas_kwargs: dict = dict(
        account_name=account_name,
        container_name=container,
        blob_name=name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
    )
    if content_type:
        sas_kwargs["content_type"] = content_type
        sas_kwargs["content_disposition"] = "inline"

    sas = generate_blob_sas(**sas_kwargs)
    encoded_name = urllib.parse.quote(name, safe="/")
    blob_url = f"https://{account_name}.blob.{endpoint_suffix}/{container}/{encoded_name}?{sas}"

    if ext in _OFFICE_EXTS:
        return "https://view.officeapps.live.com/op/view.aspx?src=" + urllib.parse.quote(blob_url, safe="")

    return blob_url
